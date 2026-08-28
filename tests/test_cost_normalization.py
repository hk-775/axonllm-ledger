"""Tests for Legacy CUR, CUR 2.0, and FOCUS schema normalization."""

from __future__ import annotations

from decimal import Decimal

from axonllm_ledger.cost_normalization import (
    CostSchema,
    detect_cost_schema,
    normalize_cost_row,
)
from axonllm_ledger.cur_ingestion import parse_line_item


def test_detects_supported_cost_schemas():
    assert detect_cost_schema({"product/servicecode": "AmazonBedrock"}) is (
        CostSchema.LEGACY_CUR
    )
    assert detect_cost_schema({"line_item_product_code": "AmazonBedrock"}) is (
        CostSchema.CUR_2
    )
    assert detect_cost_schema({"BilledCost": "1.00"}) is CostSchema.FOCUS
    assert detect_cost_schema({"unrelated": "value"}) is None


def test_normalizes_cur_2_row_and_parses_usage_record():
    row = {
        "identity_line_item_id": "cur2-line-1",
        "line_item_product_code": "AmazonBedrock",
        "line_item_usage_account_id": "123456789012",
        "line_item_iam_principal": "user-cur2",
        "line_item_resource_id": (
            "arn:aws:bedrock:us-east-1:123456789012:"
            "foundation-model/anthropic.claude-3-sonnet"
        ),
        "line_item_usage_start_date": "2026-08-01T10:00:00Z",
        "line_item_usage_end_date": "2026-08-01T11:00:00Z",
        "line_item_usage_amount": "1000",
        "line_item_net_unblended_cost": "0.42",
        "product": {
            "outputTokens": "250",
            "invocationCount": "4",
        },
        "bill_bill_type": "Anniversary",
    }

    normalized = normalize_cost_row(row)
    record = parse_line_item(row)

    assert normalized["product/servicecode"] == "AmazonBedrock"
    assert normalized["lineItem/UnblendedCost"] == "0.42"
    assert record is not None
    assert record.lineItemId == "cur2-line-1"
    assert record.userId == "user-cur2"
    assert record.modelId == "anthropic.claude-3-sonnet"
    assert record.cost == Decimal("0.42")
    assert record.inputTokens == 1000
    assert record.outputTokens == 250
    assert record.invocationCount == 4


def test_normalizes_focus_row_with_aws_extensions():
    row = {
        "BillingAccountId": "999999999999",
        "SubAccountId": "123456789012",
        "ChargePeriodStart": "2026-08-01T10:00:00+00:00",
        "ChargePeriodEnd": "2026-08-01T11:00:00+00:00",
        "BilledCost": "1.25",
        "ConsumedQuantity": "900",
        "ResourceId": (
            "arn:aws:bedrock:us-east-1:123456789012:"
            "foundation-model/amazon.nova-pro-v1:0"
        ),
        "ServiceName": "Amazon Bedrock",
        "x_ServiceCode": "AmazonBedrock",
        "x_Operation": "InvokeModel",
        "Tags": {"user:UserId": "user-focus"},
        "InvoiceId": "invoice-1",
    }

    normalized = normalize_cost_row(row)
    record = parse_line_item(row)

    assert normalized["identity/LineItemId"].startswith("focus-")
    assert record is not None
    assert record.accountId == "123456789012"
    assert record.userId == "user-focus"
    assert record.modelId == "amazon.nova-pro-v1:0"
    assert record.cost == Decimal("1.25")
    assert record.inputTokens == 900


def test_focus_generated_line_item_id_is_deterministic():
    row = {
        "SubAccountId": "123456789012",
        "ChargePeriodStart": "2026-08-01T10:00:00Z",
        "ChargePeriodEnd": "2026-08-01T11:00:00Z",
        "BilledCost": "0.10",
        "ResourceId": "resource-1",
        "SkuId": "sku-1",
    }

    first = normalize_cost_row(row)["identity/LineItemId"]
    second = normalize_cost_row(dict(row))["identity/LineItemId"]

    assert first == second


def test_cur_2_resource_tags_accept_json_text():
    row = {
        "line_item_product_code": "AmazonBedrock",
        "resource_tags": '{"axonllm:user-id": "user-json"}',
    }

    normalized = normalize_cost_row(row)

    assert normalized["resourceTags/user:UserId"] == "user-json"
