"""Normalize AWS billing schemas into Ledger's canonical CUR-shaped contract."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from enum import Enum
from typing import Any


class CostSchema(str, Enum):
    """Billing schemas accepted by the Ledger ingestion pipeline."""

    LEGACY_CUR = "legacy-cur"
    CUR_2 = "cur-2.0"
    FOCUS = "focus-1.0"


_SERVICE_CODE_ALIASES = {
    "amazon bedrock": "AmazonBedrock",
    "amazonbedrock": "AmazonBedrock",
    "amazon sagemaker": "AmazonSageMaker",
    "amazon sagemaker ai": "AmazonSageMaker",
    "amazonsagemaker": "AmazonSageMaker",
}

_USER_TAG_KEYS = (
    "user:UserId",
    "user_id",
    "userId",
    "UserId",
    "axonllm:user",
    "axonllm:user-id",
    "axonllm:user_id",
)


def detect_cost_schema(row: Mapping[str, Any]) -> CostSchema | None:
    """Detect the AWS billing schema represented by one row."""
    if any(
        key in row
        for key in (
            "product/servicecode",
            "lineItem/UsageAccountId",
            "identity/LineItemId",
        )
    ):
        return CostSchema.LEGACY_CUR
    if any(
        key in row
        for key in (
            "line_item_product_code",
            "line_item_usage_account_id",
            "identity_line_item_id",
        )
    ):
        return CostSchema.CUR_2
    if any(
        key in row
        for key in (
            "BilledCost",
            "ChargePeriodStart",
            "x_ServiceCode",
        )
    ):
        return CostSchema.FOCUS
    return None


def normalize_cost_row(
    row: Mapping[str, Any],
    *,
    schema: CostSchema | str | None = None,
) -> dict[str, Any]:
    """Return one row using the canonical keys consumed by ``parse_line_item``.

    Unknown schemas are copied unchanged so non-billing rows retain the parser's
    existing filter-and-skip behavior.
    """
    detected = CostSchema(schema) if schema is not None else detect_cost_schema(row)
    if detected in {None, CostSchema.LEGACY_CUR}:
        return dict(row)
    if detected is CostSchema.CUR_2:
        return _normalize_cur_2(row)
    return _normalize_focus(row)


def normalize_cost_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    schema: CostSchema | str | None = None,
) -> list[dict[str, Any]]:
    """Normalize a collection of AWS billing rows."""
    return [normalize_cost_row(row, schema=schema) for row in rows]


def _normalize_cur_2(row: Mapping[str, Any]) -> dict[str, Any]:
    product = _as_mapping(row.get("product"))
    tags = _as_mapping(
        _first(row, "resource_tags", "resourceTags", "tags", "Tags")
    )
    normalized = dict(row)
    normalized.update(
        {
            "product/servicecode": _normalize_service_code(
                _first(
                    row,
                    "line_item_product_code",
                    "product_servicecode",
                    "product_service_code",
                )
            ),
            "identity/LineItemId": _text(
                _first(row, "identity_line_item_id", "line_item_id")
            ),
            "lineItem/UsageAccountId": _text(
                _first(row, "line_item_usage_account_id", "usage_account_id")
            ),
            "resourceTags/user:UserId": _text(
                _first(
                    row,
                    "line_item_iam_principal",
                    "line_item_user_identifier",
                    "user_id",
                )
                or _find_user_tag(tags)
            ),
            "lineItem/ResourceId": _text(
                _first(row, "line_item_resource_id", "resource_id")
            ),
            "axonllm/modelId": _text(
                _first(
                    row,
                    "model_id",
                    "product_model_id",
                    "resource_name",
                )
                or _first(product, "modelId", "model_id", "model")
            ),
            "lineItem/UsageStartDate": _text(
                _first(row, "line_item_usage_start_date", "usage_start_date")
            ),
            "lineItem/UsageEndDate": _text(
                _first(row, "line_item_usage_end_date", "usage_end_date")
            ),
            "lineItem/UsageAmount": _text(
                _first(row, "line_item_usage_amount", "usage_amount")
            ),
            "lineItem/UnblendedCost": _text(
                _first(
                    row,
                    "line_item_net_unblended_cost",
                    "line_item_unblended_cost",
                    "unblended_cost",
                )
            ),
            "product/outputTokens": _text(
                _first(row, "product_output_tokens", "output_tokens")
                or _first(product, "outputTokens", "output_tokens")
            ),
            "product/invocationCount": _text(
                _first(row, "product_invocation_count", "invocation_count")
                or _first(product, "invocationCount", "invocation_count")
            ),
            "bill/BillType": _text(
                _first(row, "bill_bill_type", "bill_type", "bill_invoice_id")
            ),
        }
    )
    return normalized


def _normalize_focus(row: Mapping[str, Any]) -> dict[str, Any]:
    tags = _as_mapping(_first(row, "Tags", "tags"))
    resource_id = _first(row, "ResourceId", "x_ResourceId")
    line_item_id = _first(
        row,
        "x_LineItemId",
        "LineItemId",
        "ChargeId",
    ) or _focus_line_item_id(row)
    normalized = dict(row)
    normalized.update(
        {
            "product/servicecode": _normalize_service_code(
                _first(row, "x_ServiceCode", "ServiceName", "ServiceCategory")
            ),
            "identity/LineItemId": _text(line_item_id),
            "lineItem/UsageAccountId": _text(
                _first(row, "SubAccountId", "BillingAccountId")
            ),
            "resourceTags/user:UserId": _text(
                _first(
                    row,
                    "x_IamPrincipal",
                    "x_UserId",
                    "UserId",
                )
                or _find_user_tag(tags)
            ),
            "lineItem/ResourceId": _text(resource_id),
            "axonllm/modelId": _text(
                _first(
                    row,
                    "ResourceName",
                    "x_ModelId",
                    "x_ResourceName",
                )
            ),
            "lineItem/UsageStartDate": _text(
                _first(row, "ChargePeriodStart", "UsagePeriodStart")
            ),
            "lineItem/UsageEndDate": _text(
                _first(row, "ChargePeriodEnd", "UsagePeriodEnd")
            ),
            "lineItem/UsageAmount": _text(
                _first(row, "ConsumedQuantity", "PricingQuantity")
            ),
            "lineItem/UnblendedCost": _text(
                _first(row, "BilledCost", "EffectiveCost", "ListCost")
            ),
            "product/outputTokens": _text(
                _first(row, "x_OutputTokens", "OutputTokens")
            ),
            "product/invocationCount": _text(
                _first(row, "x_InvocationCount", "InvocationCount")
            ),
            "bill/BillType": _text(
                _first(row, "InvoiceId", "BillingPeriodStart")
            ),
        }
    )
    return normalized


def _focus_line_item_id(row: Mapping[str, Any]) -> str:
    stable_values = [
        _text(
            _first(
                row,
                "BillingAccountId",
                "SubAccountId",
            )
        ),
        _text(_first(row, "ChargePeriodStart", "UsagePeriodStart")),
        _text(_first(row, "ChargePeriodEnd", "UsagePeriodEnd")),
        _text(_first(row, "ResourceId", "ResourceName")),
        _text(_first(row, "SkuId", "SkuPriceId")),
        _text(_first(row, "BilledCost", "EffectiveCost", "ListCost")),
        _text(_first(row, "ConsumedQuantity", "PricingQuantity")),
    ]
    digest = hashlib.sha256(
        json.dumps(stable_values, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"focus-{digest[:32]}"


def _normalize_service_code(value: Any) -> str:
    text = _text(value).strip()
    return _SERVICE_CODE_ALIASES.get(text.lower(), text)


def _find_user_tag(tags: Mapping[str, Any]) -> Any:
    for key in _USER_TAG_KEYS:
        value = tags.get(key)
        if value is not None and value != "":
            return value
    lowered = {str(key).lower(): value for key, value in tags.items()}
    for key in _USER_TAG_KEYS:
        value = lowered.get(key.lower())
        if value is not None and value != "":
            return value
    return ""


def _first(mapping: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value is not None and value != "":
            return value
    return ""


def _as_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return {str(key): item for key, item in value.items()}
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return _as_mapping(decoded)
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        try:
            return {str(key): item for key, item in value}
        except (TypeError, ValueError):
            return {}
    return {}


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)
