"""Version-controlled Amazon Quick dashboard definition for AxonLLM Ledger."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

DATASET_IDENTIFIERS = (
    "cost_aggregations",
    "model_access",
    "budgets",
    "optimization_recommendations",
)

_COST_DATASET = "cost_aggregations"
_ACCESS_DATASET = "model_access"
_BUDGET_DATASET = "budgets"
_OPTIMIZATION_DATASET = "optimization_recommendations"

_PURPLE = "#6750A4"
_BLUE = "#1473E6"
_TEAL = "#00897B"
_GREEN = "#2E7D32"
_ORANGE = "#EF6C00"


def build_dashboard_definition(
    data_set_arns: Mapping[str, str],
) -> dict[str, Any]:
    """Build the six-sheet AxonLLM Ledger dashboard definition.

    The returned dictionary is accepted by the ``Definition`` parameter of the
    Quick Sight ``CreateDashboard`` and ``UpdateDashboard`` APIs.
    """
    missing = sorted(set(DATASET_IDENTIFIERS) - set(data_set_arns))
    if missing:
        raise ValueError(f"missing data set ARNs: {', '.join(missing)}")

    invalid = sorted(
        identifier
        for identifier in DATASET_IDENTIFIERS
        if not data_set_arns[identifier].startswith("arn:aws:quicksight:")
    )
    if invalid:
        raise ValueError(
            "data set ARNs must be Amazon Quick Sight ARNs: "
            + ", ".join(invalid)
        )

    executive_visuals = _executive_visuals()
    model_visuals = _model_visuals()
    organization_visuals = _organization_visuals()
    budget_visuals = _budget_visuals()
    optimization_visuals = _optimization_visuals()
    quality_visuals = _quality_visuals()

    sheets = [
        _sheet(
            "executive-overview",
            "Executive overview",
            executive_visuals,
            (
                ("exec-total-spend", 0, 0, 12, 4),
                ("exec-total-invocations", 12, 0, 12, 4),
                ("exec-potential-savings", 24, 0, 12, 4),
                ("exec-spend-trend", 0, 4, 18, 12),
                ("exec-spend-by-account", 18, 4, 18, 12),
                ("exec-budget-comparison", 0, 16, 36, 12),
            ),
        ),
        _sheet(
            "model-economics",
            "Model economics",
            model_visuals,
            (
                ("model-cost", 0, 0, 18, 12),
                ("model-invocations", 18, 0, 18, 12),
                ("model-token-volume", 0, 12, 36, 12),
                ("model-details", 0, 24, 36, 12),
            ),
        ),
        _sheet(
            "organizational-allocation",
            "Organizational allocation",
            organization_visuals,
            (
                ("org-cost-by-ou", 0, 0, 12, 12),
                ("org-cost-by-account", 12, 0, 12, 12),
                ("org-cost-by-user", 24, 0, 12, 12),
                ("org-model-access", 0, 12, 36, 14),
            ),
        ),
        _sheet(
            "budgets",
            "Budgets",
            budget_visuals,
            (
                ("budget-total-limit", 0, 0, 12, 4),
                ("budget-total-actual", 12, 0, 12, 4),
                ("budget-total-forecast", 24, 0, 12, 4),
                ("budget-comparison", 0, 4, 36, 12),
                ("budget-details", 0, 16, 36, 14),
            ),
        ),
        _sheet(
            "optimization",
            "Optimization",
            optimization_visuals,
            (
                ("optimization-total-savings", 0, 0, 12, 4),
                ("optimization-by-type", 0, 4, 18, 12),
                ("optimization-by-account", 18, 4, 18, 12),
                ("optimization-details", 0, 16, 36, 14),
            ),
        ),
        _sheet(
            "data-quality",
            "Data quality",
            quality_visuals,
            (
                ("quality-cost-rows", 0, 0, 9, 4),
                ("quality-access-rows", 9, 0, 9, 4),
                ("quality-budget-rows", 18, 0, 9, 4),
                ("quality-recommendation-rows", 27, 0, 9, 4),
                ("quality-period-coverage", 0, 4, 36, 14),
            ),
        ),
    ]

    return {
        "DataSetIdentifierDeclarations": [
            {
                "Identifier": identifier,
                "DataSetArn": data_set_arns[identifier],
            }
            for identifier in DATASET_IDENTIFIERS
        ],
        "Sheets": sheets,
        "FilterGroups": [
            _dimension_filter(
                "executive-account-cost-filter",
                "ACCOUNT",
                {
                    "executive-overview": (
                        "exec-total-spend",
                        "exec-total-invocations",
                        "exec-spend-trend",
                        "exec-spend-by-account",
                    )
                },
            ),
            _dimension_filter(
                "organization-account-cost-filter",
                "ACCOUNT",
                {"organizational-allocation": ("org-cost-by-account",)},
            ),
            _dimension_filter(
                "model-cost-filter",
                "MODEL",
                {
                    "model-economics": (
                        "model-cost",
                        "model-invocations",
                        "model-token-volume",
                        "model-details",
                    )
                },
            ),
            _dimension_filter(
                "ou-cost-filter",
                "ORGANIZATIONAL_UNIT",
                {"organizational-allocation": ("org-cost-by-ou",)},
            ),
            _dimension_filter(
                "user-cost-filter",
                "USER",
                {"organizational-allocation": ("org-cost-by-user",)},
            ),
        ],
        "Options": {
            "Timezone": "UTC",
            "WeekStart": "MONDAY",
        },
    }


def _executive_visuals() -> list[dict[str, Any]]:
    return [
        _kpi(
            "exec-total-spend",
            "Total billed AI spend",
            _numerical_measure(
                _COST_DATASET,
                "total_cost_usd",
                "exec-total-spend-value",
                currency=True,
            ),
        ),
        _kpi(
            "exec-total-invocations",
            "Total invocations",
            _numerical_measure(
                _COST_DATASET,
                "total_invocations",
                "exec-total-invocations-value",
            ),
        ),
        _kpi(
            "exec-potential-savings",
            "Potential savings",
            _numerical_measure(
                _OPTIMIZATION_DATASET,
                "estimated_savings_usd",
                "exec-potential-savings-value",
                currency=True,
            ),
        ),
        _line_chart(
            "exec-spend-trend",
            "Spend trend",
            _date_dimension(
                _COST_DATASET,
                "period_start",
                "exec-spend-trend-period",
            ),
            (
                _numerical_measure(
                    _COST_DATASET,
                    "total_cost_usd",
                    "exec-spend-trend-cost",
                    currency=True,
                ),
            ),
            color=_PURPLE,
        ),
        _bar_chart(
            "exec-spend-by-account",
            "Spend by AWS account",
            _categorical_dimension(
                _COST_DATASET,
                "dimension_value",
                "exec-account",
            ),
            (
                _numerical_measure(
                    _COST_DATASET,
                    "total_cost_usd",
                    "exec-account-cost",
                    currency=True,
                ),
            ),
            color=_BLUE,
        ),
        _bar_chart(
            "exec-budget-comparison",
            "Budget limit, actual, and forecast",
            _categorical_dimension(
                _BUDGET_DATASET,
                "budget_name",
                "exec-budget-name",
            ),
            (
                _numerical_measure(
                    _BUDGET_DATASET,
                    "budget_limit_usd",
                    "exec-budget-limit",
                    currency=True,
                ),
                _numerical_measure(
                    _BUDGET_DATASET,
                    "actual_spend_usd",
                    "exec-budget-actual",
                    currency=True,
                ),
                _numerical_measure(
                    _BUDGET_DATASET,
                    "forecasted_spend_usd",
                    "exec-budget-forecast",
                    currency=True,
                ),
            ),
            color=_ORANGE,
            horizontal=False,
        ),
    ]


def _model_visuals() -> list[dict[str, Any]]:
    model = _categorical_dimension(
        _COST_DATASET,
        "dimension_value",
        "model-name",
    )
    return [
        _bar_chart(
            "model-cost",
            "Cost by model or endpoint",
            model,
            (
                _numerical_measure(
                    _COST_DATASET,
                    "total_cost_usd",
                    "model-cost-value",
                    currency=True,
                ),
            ),
            color=_PURPLE,
        ),
        _bar_chart(
            "model-invocations",
            "Invocations by model",
            _categorical_dimension(
                _COST_DATASET,
                "dimension_value",
                "model-invocations-name",
            ),
            (
                _numerical_measure(
                    _COST_DATASET,
                    "total_invocations",
                    "model-invocations-value",
                ),
            ),
            color=_BLUE,
        ),
        _bar_chart(
            "model-token-volume",
            "Input and output token volume",
            _categorical_dimension(
                _COST_DATASET,
                "dimension_value",
                "model-token-name",
            ),
            (
                _numerical_measure(
                    _COST_DATASET,
                    "total_input_tokens",
                    "model-input-tokens",
                ),
                _numerical_measure(
                    _COST_DATASET,
                    "total_output_tokens",
                    "model-output-tokens",
                ),
            ),
            color=_TEAL,
            horizontal=False,
        ),
        _table(
            "model-details",
            "Model economics details",
            (
                _categorical_dimension(
                    _COST_DATASET,
                    "dimension_value",
                    "model-details-name",
                ),
            ),
            (
                _numerical_measure(
                    _COST_DATASET,
                    "total_cost_usd",
                    "model-details-cost",
                    currency=True,
                ),
                _numerical_measure(
                    _COST_DATASET,
                    "total_invocations",
                    "model-details-invocations",
                ),
                _numerical_measure(
                    _COST_DATASET,
                    "total_input_tokens",
                    "model-details-input",
                ),
                _numerical_measure(
                    _COST_DATASET,
                    "total_output_tokens",
                    "model-details-output",
                ),
            ),
        ),
    ]


def _organization_visuals() -> list[dict[str, Any]]:
    return [
        _bar_chart(
            "org-cost-by-ou",
            "Cost by organizational unit",
            _categorical_dimension(
                _COST_DATASET,
                "dimension_value",
                "org-ou-name",
            ),
            (
                _numerical_measure(
                    _COST_DATASET,
                    "total_cost_usd",
                    "org-ou-cost",
                    currency=True,
                ),
            ),
            color=_PURPLE,
        ),
        _bar_chart(
            "org-cost-by-account",
            "Cost by AWS account",
            _categorical_dimension(
                _COST_DATASET,
                "dimension_value",
                "org-account-name",
            ),
            (
                _numerical_measure(
                    _COST_DATASET,
                    "total_cost_usd",
                    "org-account-cost",
                    currency=True,
                ),
            ),
            color=_BLUE,
        ),
        _bar_chart(
            "org-cost-by-user",
            "Cost by user",
            _categorical_dimension(
                _COST_DATASET,
                "dimension_value",
                "org-user-name",
            ),
            (
                _numerical_measure(
                    _COST_DATASET,
                    "total_cost_usd",
                    "org-user-cost",
                    currency=True,
                ),
            ),
            color=_TEAL,
        ),
        _table(
            "org-model-access",
            "User-to-model access",
            (
                _categorical_dimension(
                    _ACCESS_DATASET,
                    "user_id",
                    "org-access-user",
                ),
                _categorical_dimension(
                    _ACCESS_DATASET,
                    "model_id",
                    "org-access-model",
                ),
            ),
            (),
        ),
    ]


def _budget_visuals() -> list[dict[str, Any]]:
    return [
        _kpi(
            "budget-total-limit",
            "Total budget limit",
            _numerical_measure(
                _BUDGET_DATASET,
                "budget_limit_usd",
                "budget-limit-value",
                currency=True,
            ),
        ),
        _kpi(
            "budget-total-actual",
            "Actual spend",
            _numerical_measure(
                _BUDGET_DATASET,
                "actual_spend_usd",
                "budget-actual-value",
                currency=True,
            ),
        ),
        _kpi(
            "budget-total-forecast",
            "Forecasted spend",
            _numerical_measure(
                _BUDGET_DATASET,
                "forecasted_spend_usd",
                "budget-forecast-value",
                currency=True,
            ),
        ),
        _bar_chart(
            "budget-comparison",
            "Budget utilization",
            _categorical_dimension(
                _BUDGET_DATASET,
                "budget_name",
                "budget-comparison-name",
            ),
            (
                _numerical_measure(
                    _BUDGET_DATASET,
                    "budget_limit_usd",
                    "budget-comparison-limit",
                    currency=True,
                ),
                _numerical_measure(
                    _BUDGET_DATASET,
                    "actual_spend_usd",
                    "budget-comparison-actual",
                    currency=True,
                ),
                _numerical_measure(
                    _BUDGET_DATASET,
                    "forecasted_spend_usd",
                    "budget-comparison-forecast",
                    currency=True,
                ),
            ),
            color=_ORANGE,
            horizontal=False,
        ),
        _table(
            "budget-details",
            "Budget details",
            (
                _categorical_dimension(
                    _BUDGET_DATASET,
                    "budget_name",
                    "budget-details-name",
                ),
                _categorical_dimension(
                    _BUDGET_DATASET,
                    "account_id",
                    "budget-details-account",
                ),
            ),
            (
                _numerical_measure(
                    _BUDGET_DATASET,
                    "budget_limit_usd",
                    "budget-details-limit",
                    currency=True,
                ),
                _numerical_measure(
                    _BUDGET_DATASET,
                    "actual_spend_usd",
                    "budget-details-actual",
                    currency=True,
                ),
                _numerical_measure(
                    _BUDGET_DATASET,
                    "forecasted_spend_usd",
                    "budget-details-forecast",
                    currency=True,
                ),
            ),
        ),
    ]


def _optimization_visuals() -> list[dict[str, Any]]:
    return [
        _kpi(
            "optimization-total-savings",
            "Estimated savings",
            _numerical_measure(
                _OPTIMIZATION_DATASET,
                "estimated_savings_usd",
                "optimization-savings-value",
                currency=True,
            ),
        ),
        _bar_chart(
            "optimization-by-type",
            "Savings by recommendation type",
            _categorical_dimension(
                _OPTIMIZATION_DATASET,
                "recommendation_type",
                "optimization-type",
            ),
            (
                _numerical_measure(
                    _OPTIMIZATION_DATASET,
                    "estimated_savings_usd",
                    "optimization-type-savings",
                    currency=True,
                ),
            ),
            color=_GREEN,
        ),
        _bar_chart(
            "optimization-by-account",
            "Savings by AWS account",
            _categorical_dimension(
                _OPTIMIZATION_DATASET,
                "account_id",
                "optimization-account",
            ),
            (
                _numerical_measure(
                    _OPTIMIZATION_DATASET,
                    "estimated_savings_usd",
                    "optimization-account-savings",
                    currency=True,
                ),
            ),
            color=_TEAL,
        ),
        _table(
            "optimization-details",
            "Prioritized recommendations",
            (
                _categorical_dimension(
                    _OPTIMIZATION_DATASET,
                    "recommendation_type",
                    "optimization-details-type",
                ),
                _categorical_dimension(
                    _OPTIMIZATION_DATASET,
                    "account_id",
                    "optimization-details-account",
                ),
                _categorical_dimension(
                    _OPTIMIZATION_DATASET,
                    "model_id",
                    "optimization-details-model",
                ),
                _categorical_dimension(
                    _OPTIMIZATION_DATASET,
                    "description",
                    "optimization-details-description",
                ),
            ),
            (
                _numerical_measure(
                    _OPTIMIZATION_DATASET,
                    "estimated_savings_usd",
                    "optimization-details-savings",
                    currency=True,
                ),
            ),
        ),
    ]


def _quality_visuals() -> list[dict[str, Any]]:
    return [
        _kpi(
            "quality-cost-rows",
            "Cost aggregation rows",
            _categorical_measure(
                _COST_DATASET,
                "dimension_value",
                "quality-cost-row-count",
            ),
        ),
        _kpi(
            "quality-access-rows",
            "Access relationships",
            _categorical_measure(
                _ACCESS_DATASET,
                "model_id",
                "quality-access-row-count",
            ),
        ),
        _kpi(
            "quality-budget-rows",
            "Budget records",
            _categorical_measure(
                _BUDGET_DATASET,
                "budget_id",
                "quality-budget-row-count",
            ),
        ),
        _kpi(
            "quality-recommendation-rows",
            "Optimization records",
            _categorical_measure(
                _OPTIMIZATION_DATASET,
                "recommendation_id",
                "quality-recommendation-row-count",
            ),
        ),
        _table(
            "quality-period-coverage",
            "Cost dataset period coverage",
            (
                _date_dimension(
                    _COST_DATASET,
                    "period_start",
                    "quality-period-start",
                ),
                _date_dimension(
                    _COST_DATASET,
                    "period_end",
                    "quality-period-end",
                ),
                _categorical_dimension(
                    _COST_DATASET,
                    "dimension_type",
                    "quality-dimension-type",
                ),
            ),
            (
                _categorical_measure(
                    _COST_DATASET,
                    "dimension_value",
                    "quality-period-row-count",
                ),
            ),
        ),
    ]


def _sheet(
    sheet_id: str,
    name: str,
    visuals: Sequence[dict[str, Any]],
    positions: Sequence[tuple[str, int, int, int, int]],
) -> dict[str, Any]:
    visual_ids = {_visual_id(visual) for visual in visuals}
    positioned_ids = {position[0] for position in positions}
    if visual_ids != positioned_ids:
        raise ValueError(f"layout elements do not match visuals for {sheet_id}")

    return {
        "SheetId": sheet_id,
        "Name": name,
        "ContentType": "INTERACTIVE",
        "Visuals": list(visuals),
        "Layouts": [
            {
                "Configuration": {
                    "GridLayout": {
                        "Elements": [
                            {
                                "ElementId": visual_id,
                                "ElementType": "VISUAL",
                                "ColumnIndex": column,
                                "ColumnSpan": column_span,
                                "RowIndex": row,
                                "RowSpan": row_span,
                            }
                            for (
                                visual_id,
                                column,
                                row,
                                column_span,
                                row_span,
                            ) in positions
                        ],
                        "CanvasSizeOptions": {
                            "ScreenCanvasSizeOptions": {
                                "ResizeOption": "RESPONSIVE"
                            }
                        },
                    }
                }
            }
        ],
    }


def _kpi(
    visual_id: str,
    title: str,
    value: dict[str, Any],
) -> dict[str, Any]:
    return {
        "KPIVisual": {
            "VisualId": visual_id,
            "Title": _title(title),
            "ChartConfiguration": {
                "FieldWells": {"Values": [value]},
            },
            "VisualContentAltText": title,
        }
    }


def _bar_chart(
    visual_id: str,
    title: str,
    category: dict[str, Any],
    values: Sequence[dict[str, Any]],
    *,
    color: str,
    horizontal: bool = True,
) -> dict[str, Any]:
    return {
        "BarChartVisual": {
            "VisualId": visual_id,
            "Title": _title(title),
            "ChartConfiguration": {
                "FieldWells": {
                    "BarChartAggregatedFieldWells": {
                        "Category": [category],
                        "Values": list(values),
                    }
                },
                "Orientation": "HORIZONTAL" if horizontal else "VERTICAL",
                "BarsArrangement": "CLUSTERED",
                "VisualPalette": {"ChartColor": color},
                "Legend": {
                    "Visibility": "VISIBLE" if len(values) > 1 else "HIDDEN",
                    "Position": "BOTTOM",
                },
                "DataLabels": {"Visibility": "VISIBLE"},
            },
            "VisualContentAltText": title,
        }
    }


def _line_chart(
    visual_id: str,
    title: str,
    category: dict[str, Any],
    values: Sequence[dict[str, Any]],
    *,
    color: str,
) -> dict[str, Any]:
    return {
        "LineChartVisual": {
            "VisualId": visual_id,
            "Title": _title(title),
            "ChartConfiguration": {
                "FieldWells": {
                    "LineChartAggregatedFieldWells": {
                        "Category": [category],
                        "Values": list(values),
                    }
                },
                "Type": "LINE",
                "VisualPalette": {"ChartColor": color},
                "Legend": {"Visibility": "HIDDEN"},
                "DataLabels": {"Visibility": "VISIBLE"},
            },
            "VisualContentAltText": title,
        }
    }


def _table(
    visual_id: str,
    title: str,
    group_by: Sequence[dict[str, Any]],
    values: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "TableVisual": {
            "VisualId": visual_id,
            "Title": _title(title),
            "ChartConfiguration": {
                "FieldWells": {
                    "TableAggregatedFieldWells": {
                        "GroupBy": list(group_by),
                        "Values": list(values),
                    }
                },
                "TableOptions": {
                    "Orientation": "VERTICAL",
                    "HeaderStyle": {
                        "Visibility": "VISIBLE",
                        "TextWrap": "WRAP",
                        "BackgroundColor": "#EDE7F6",
                    },
                    "RowAlternateColorOptions": {"Status": "ENABLED"},
                },
                "TotalOptions": {
                    "TotalsVisibility": "VISIBLE" if values else "HIDDEN",
                    "Placement": "END",
                },
            },
            "VisualContentAltText": title,
        }
    }


def _title(text: str) -> dict[str, Any]:
    return {
        "Visibility": "VISIBLE",
        "FormatText": {"PlainText": text},
    }


def _column(data_set_identifier: str, column_name: str) -> dict[str, str]:
    return {
        "DataSetIdentifier": data_set_identifier,
        "ColumnName": column_name,
    }


def _categorical_dimension(
    data_set_identifier: str,
    column_name: str,
    field_id: str,
) -> dict[str, Any]:
    return {
        "CategoricalDimensionField": {
            "FieldId": field_id,
            "Column": _column(data_set_identifier, column_name),
        }
    }


def _date_dimension(
    data_set_identifier: str,
    column_name: str,
    field_id: str,
) -> dict[str, Any]:
    return {
        "DateDimensionField": {
            "FieldId": field_id,
            "Column": _column(data_set_identifier, column_name),
            "DateGranularity": "DAY",
        }
    }


def _numerical_measure(
    data_set_identifier: str,
    column_name: str,
    field_id: str,
    *,
    currency: bool = False,
) -> dict[str, Any]:
    field: dict[str, Any] = {
        "FieldId": field_id,
        "Column": _column(data_set_identifier, column_name),
        "AggregationFunction": {"SimpleNumericalAggregation": "SUM"},
    }
    if currency:
        field["FormatConfiguration"] = {
            "FormatConfiguration": {
                "CurrencyDisplayFormatConfiguration": {
                    "Symbol": "USD",
                    "DecimalPlacesConfiguration": {"DecimalPlaces": 2},
                }
            }
        }
    return {"NumericalMeasureField": field}


def _categorical_measure(
    data_set_identifier: str,
    column_name: str,
    field_id: str,
) -> dict[str, Any]:
    return {
        "CategoricalMeasureField": {
            "FieldId": field_id,
            "Column": _column(data_set_identifier, column_name),
            "AggregationFunction": "COUNT",
        }
    }


def _dimension_filter(
    filter_group_id: str,
    value: str,
    visual_scopes: Mapping[str, Sequence[str]],
) -> dict[str, Any]:
    return {
        "FilterGroupId": filter_group_id,
        "Filters": [
            {
                "CategoryFilter": {
                    "FilterId": f"{filter_group_id}-value",
                    "Column": _column(_COST_DATASET, "dimension_type"),
                    "Configuration": {
                        "FilterListConfiguration": {
                            "MatchOperator": "CONTAINS",
                            "CategoryValues": [value],
                            "NullOption": "NON_NULLS_ONLY",
                        }
                    },
                }
            }
        ],
        "ScopeConfiguration": {
            "SelectedSheets": {
                "SheetVisualScopingConfigurations": [
                    {
                        "SheetId": sheet_id,
                        "Scope": "SELECTED_VISUALS",
                        "VisualIds": list(visual_ids),
                    }
                    for sheet_id, visual_ids in visual_scopes.items()
                ]
            }
        },
        "Status": "ENABLED",
        "CrossDataset": "SINGLE_DATASET",
    }


def _visual_id(visual: Mapping[str, Any]) -> str:
    if len(visual) != 1:
        raise ValueError("each visual must contain exactly one visual type")
    return next(iter(visual.values()))["VisualId"]
