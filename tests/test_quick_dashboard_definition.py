"""Tests for the version-controlled Amazon Quick dashboard definition."""

from __future__ import annotations

from botocore.session import Session
from botocore.validate import ParamValidator
import pytest

from axonllm_ledger.quick_dashboard_definition import (
    DATASET_IDENTIFIERS,
    build_dashboard_definition,
)


def _data_set_arns() -> dict[str, str]:
    return {
        identifier: (
            "arn:aws:quicksight:us-east-1:123456789012:"
            f"dataset/axonllm-ledger-{identifier.replace('_', '-')}"
        )
        for identifier in DATASET_IDENTIFIERS
    }


def test_builds_six_sheet_dashboard_with_expected_visuals() -> None:
    definition = build_dashboard_definition(_data_set_arns())

    assert [sheet["Name"] for sheet in definition["Sheets"]] == [
        "Executive overview",
        "Model economics",
        "Organizational allocation",
        "Budgets",
        "Optimization",
        "Data quality",
    ]
    assert sum(len(sheet["Visuals"]) for sheet in definition["Sheets"]) == 28
    assert len(definition["FilterGroups"]) == 5


def test_every_visual_has_one_matching_grid_element() -> None:
    definition = build_dashboard_definition(_data_set_arns())

    for sheet in definition["Sheets"]:
        visual_ids = {
            next(iter(visual.values()))["VisualId"]
            for visual in sheet["Visuals"]
        }
        elements = sheet["Layouts"][0]["Configuration"]["GridLayout"][
            "Elements"
        ]
        assert {element["ElementId"] for element in elements} == visual_ids


def test_dimension_filters_use_list_membership_configuration() -> None:
    definition = build_dashboard_definition(_data_set_arns())

    for group in definition["FilterGroups"]:
        configuration = group["Filters"][0]["CategoryFilter"][
            "Configuration"
        ]
        list_filter = configuration["FilterListConfiguration"]
        assert list_filter["MatchOperator"] == "CONTAINS"
        assert list_filter["NullOption"] == "NON_NULLS_ONLY"


def test_definition_validates_against_current_botocore_model() -> None:
    request = {
        "AwsAccountId": "123456789012",
        "DashboardId": "axonllm-ledger",
        "Name": "AxonLLM Ledger",
        "Definition": build_dashboard_definition(_data_set_arns()),
        "ValidationStrategy": {"Mode": "STRICT"},
    }
    operation = Session().get_service_model("quicksight").operation_model(
        "CreateDashboard"
    )
    errors = ParamValidator().validate(request, operation.input_shape)

    assert not errors.has_errors(), errors.generate_report()


def test_rejects_missing_data_set_arn() -> None:
    arns = _data_set_arns()
    del arns["budgets"]

    with pytest.raises(ValueError, match="missing data set ARNs: budgets"):
        build_dashboard_definition(arns)


def test_rejects_non_quick_data_set_arn() -> None:
    arns = _data_set_arns()
    arns["budgets"] = "arn:aws:s3:::not-a-data-set"

    with pytest.raises(ValueError, match="budgets"):
        build_dashboard_definition(arns)
