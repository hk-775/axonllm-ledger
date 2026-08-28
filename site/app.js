"use strict";

const SHEETS = {
  "executive-overview": {
    title: "Executive overview",
    description: "Portfolio-level AI spend, usage, savings, and budget posture.",
    render: renderExecutiveOverview,
  },
  "model-economics": {
    title: "Model economics",
    description:
      "Compare model cost, invocation volume, token usage, and unit economics.",
    render: renderModelEconomics,
  },
  "organizational-allocation": {
    title: "Organizational allocation",
    description:
      "Trace generative AI spend across organizational units, accounts, and users.",
    render: renderOrganizationalAllocation,
  },
  budgets: {
    title: "Budgets",
    description:
      "Track actual and forecasted spend against account-level guardrails.",
    render: renderBudgets,
  },
  optimization: {
    title: "Optimization",
    description:
      "Prioritize actionable savings opportunities from Cost Optimization Hub.",
    render: renderOptimization,
  },
  "data-quality": {
    title: "Data quality",
    description:
      "Verify ingestion health, analytics-table coverage, and reporting completeness.",
    render: renderDataQuality,
  },
};

const MODEL_LABELS = {
  "anthropic.claude-3-sonnet": "Claude 3 Sonnet",
  "anthropic.claude-3-haiku": "Claude 3 Haiku",
  "anthropic.claude-3-opus": "Claude 3 Opus",
  "amazon.titan-text-express": "Titan Text Express",
  "amazon.titan-embed-text": "Titan Text Embeddings",
  "meta.llama3-70b-instruct": "Llama 3 70B Instruct",
  "stability.stable-diffusion-xl": "Stable Diffusion XL",
  "genai-endpoint-1": "SageMaker endpoint 1",
  "genai-finetuned-model": "Fine-tuned endpoint",
};

const COLORS = {
  purple: "#6750a4",
  blue: "#1473e6",
  teal: "#00897b",
  green: "#2e7d32",
  orange: "#d96400",
  red: "#ba1a1a",
};

const numberFormatter = new Intl.NumberFormat("en-US");
const compactFormatter = new Intl.NumberFormat("en-US", {
  notation: "compact",
  maximumFractionDigits: 1,
});
const currencyFormatter = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});
const preciseCurrencyFormatter = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 4,
  maximumFractionDigits: 4,
});

const state = {
  data: null,
  sheet: getInitialSheet(),
  accountId: "all",
};

document.addEventListener("DOMContentLoaded", initialize);

async function initialize() {
  wireNavigation();
  wireTheme();

  try {
    const response = await fetch("data/dashboard.json", { cache: "no-store" });
    if (!response.ok) {
      throw new Error(`dashboard data returned ${response.status}`);
    }
    state.data = await response.json();
    populateAccountFilter();
    updateReportingPeriod();
    render();
  } catch (error) {
    console.error(error);
    document.querySelector("#dashboard-content").innerHTML = `
      <div class="error-state" role="alert">
        <div>
          <strong>The sample dashboard could not load.</strong>
          <span>
            Generate <code>site/data/dashboard.json</code> with
            <code>python scripts/build_pages_data.py</code>, then serve the
            repository over HTTP.
          </span>
        </div>
      </div>
    `;
  }
}

function getInitialSheet() {
  const requested = new URLSearchParams(window.location.search).get("sheet");
  return Object.hasOwn(SHEETS, requested) ? requested : "executive-overview";
}

function wireNavigation() {
  document.querySelectorAll("[data-sheet]").forEach((button) => {
    button.addEventListener("click", () => {
      const sheet = button.dataset.sheet;
      if (!Object.hasOwn(SHEETS, sheet) || sheet === state.sheet) {
        return;
      }
      state.sheet = sheet;
      const url = new URL(window.location.href);
      url.searchParams.set("sheet", sheet);
      window.history.replaceState({}, "", url);
      render();
      document.querySelector("#dashboard-content").focus({ preventScroll: true });
      window.scrollTo({ top: 0, behavior: "smooth" });
    });
  });

  document.querySelector("#account-filter").addEventListener("change", (event) => {
    state.accountId = event.target.value;
    render();
  });
}

function wireTheme() {
  const savedTheme = window.localStorage.getItem("axonllm-theme");
  const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
  applyTheme(savedTheme || (prefersDark ? "dark" : "light"));

  document.querySelector("#theme-toggle").addEventListener("click", () => {
    const current = document.documentElement.dataset.theme;
    const next = current === "dark" ? "light" : "dark";
    window.localStorage.setItem("axonllm-theme", next);
    applyTheme(next);
  });
}

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  const button = document.querySelector("#theme-toggle");
  button.setAttribute(
    "aria-label",
    theme === "dark" ? "Use light theme" : "Use dark theme",
  );
}

function populateAccountFilter() {
  const select = document.querySelector("#account-filter");
  const options = state.data.accounts
    .slice()
    .sort((left, right) => left.account_name.localeCompare(right.account_name))
    .map(
      (account) =>
        `<option value="${escapeHtml(account.account_id)}">${escapeHtml(
          account.account_name,
        )}</option>`,
    )
    .join("");
  select.innerHTML = `<option value="all">All AWS accounts</option>${options}`;
  select.disabled = false;
}

function updateReportingPeriod() {
  const { start, end } = state.data.period;
  document.querySelector("#period-label").textContent = formatDateRange(start, end);
}

function render() {
  if (!state.data) {
    return;
  }

  const definition = SHEETS[state.sheet];
  document.title = `${definition.title} · AxonLLM Ledger`;
  document.querySelector("#sheet-title").textContent = definition.title;
  document.querySelector("#sheet-description").textContent = definition.description;
  document.querySelectorAll("[data-sheet]").forEach((button) => {
    const active = button.dataset.sheet === state.sheet;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-current", active ? "page" : "false");
  });

  const context = getContext();
  document.querySelector("#dashboard-content").innerHTML =
    `<section class="dashboard-section">${definition.render(context)}</section>`;
}

function getContext() {
  const records =
    state.accountId === "all"
      ? state.data.records
      : state.data.records.filter(
          (record) => record.account_id === state.accountId,
        );
  const budgets =
    state.accountId === "all"
      ? state.data.budgets
      : state.data.budgets.filter(
          (budget) => budget.account_id === state.accountId,
        );
  const recommendations =
    state.accountId === "all"
      ? state.data.recommendations
      : state.data.recommendations.filter(
          (recommendation) => recommendation.account_id === state.accountId,
        );
  const modelAccess =
    state.accountId === "all"
      ? state.data.model_access
      : state.data.model_access.filter(
          (relationship) => relationship.account_id === state.accountId,
        );

  return {
    all: state.data,
    records,
    budgets,
    recommendations,
    modelAccess,
    accountId: state.accountId,
    accountLabel:
      state.accountId === "all"
        ? "All AWS accounts"
        : accountName(state.accountId),
  };
}

function renderExecutiveOverview(context) {
  const totalSpend = sum(context.records, "cost_usd");
  const totalInvocations = sum(context.records, "invocations");
  const savings = sum(context.recommendations, "estimated_savings_usd");
  const trend = aggregate(context.records, "timestamp", "cost_usd");
  const accountSpend = aggregate(context.records, "account_id", "cost_usd").map(
    (row) => ({ ...row, label: accountName(row.key) }),
  );

  return `
    <div class="kpi-grid">
      ${kpiCard({
        label: "Total billed AI spend",
        value: money(totalSpend, true),
        helper: `${numberFormatter.format(context.records.length)} normalized usage rows`,
        signal: `${unique(context.records, "account_id")} accounts`,
        symbol: "$",
        tone: "purple",
      })}
      ${kpiCard({
        label: "Total invocations",
        value: numberFormatter.format(totalInvocations),
        helper: `${compactFormatter.format(sum(context.records, "input_tokens") + sum(context.records, "output_tokens"))} tokens processed`,
        signal: `${unique(context.records, "model_id")} models`,
        symbol: "↯",
        tone: "blue",
      })}
      ${kpiCard({
        label: "Potential savings",
        value: money(savings),
        helper: `${context.recommendations.length} prioritized recommendations`,
        signal: "Cost Optimization Hub",
        symbol: "↗",
        tone: "green",
      })}
    </div>
    <div class="visual-grid">
      ${panel({
        title: "Spend trend",
        subtitle: "Hourly normalized cost across the sample reporting window",
        tag: context.accountLabel,
        span: 8,
        content: lineChart(trend),
      })}
      ${panel({
        title: "Spend by AWS account",
        subtitle: "Allocated from CUR line items and Organizations metadata",
        tag: `${accountSpend.length} accounts`,
        span: 4,
        content: horizontalBars(accountSpend, {
          color: COLORS.blue,
          value: (row) => money(row.value, true),
          limit: 6,
        }),
      })}
      ${panel({
        title: "Budget limit, actual, and forecast",
        subtitle: "Monthly budget posture for the selected account scope",
        tag: budgetHealthLabel(context.budgets),
        span: 12,
        legend: [
          ["Limit", COLORS.purple],
          ["Actual", COLORS.blue],
          ["Forecast", COLORS.orange],
        ],
        content: budgetComparison(context.budgets),
      })}
    </div>
  `;
}

function renderModelEconomics(context) {
  const modelCost = aggregate(context.records, "model_id", "cost_usd").map(
    withModelLabel,
  );
  const modelInvocations = aggregate(
    context.records,
    "model_id",
    "invocations",
  ).map(withModelLabel);
  const modelTokens = aggregateMultiple(context.records, "model_id", [
    "input_tokens",
    "output_tokens",
  ]).map(withModelLabel);
  const modelRows = aggregateMultiple(context.records, "model_id", [
    "cost_usd",
    "invocations",
    "input_tokens",
    "output_tokens",
  ]).sort((left, right) => right.cost_usd - left.cost_usd);

  return `
    ${summaryStrip([
      [money(sum(context.records, "cost_usd"), true), "billed spend"],
      [numberFormatter.format(sum(context.records, "invocations")), "invocations"],
      [compactFormatter.format(sum(context.records, "input_tokens")), "input tokens"],
      [compactFormatter.format(sum(context.records, "output_tokens")), "output tokens"],
    ])}
    <div class="visual-grid">
      ${panel({
        title: "Cost by model or endpoint",
        subtitle: "Normalized billed cost in USD",
        tag: `${modelCost.length} resources`,
        span: 6,
        content: horizontalBars(modelCost, {
          color: COLORS.purple,
          value: (row) => money(row.value, true),
        }),
      })}
      ${panel({
        title: "Invocations by model",
        subtitle: "Request volume across Bedrock and SageMaker",
        tag: `${numberFormatter.format(sum(context.records, "invocations"))} total`,
        span: 6,
        content: horizontalBars(modelInvocations, {
          color: COLORS.blue,
          value: (row) => numberFormatter.format(row.value),
        }),
      })}
      ${panel({
        title: "Input and output token volume",
        subtitle: "Token mix by model; SageMaker endpoint usage is retained",
        tag: "Input + output",
        span: 12,
        legend: [
          ["Input tokens", COLORS.teal],
          ["Output tokens", COLORS.purple],
        ],
        content: tokenChart(modelTokens),
      })}
      ${panel({
        title: "Model economics details",
        subtitle: "Unit economics for every model and managed endpoint",
        tag: "Sortable in Amazon Quick",
        span: 12,
        content: modelTable(modelRows),
      })}
    </div>
  `;
}

function renderOrganizationalAllocation(context) {
  const ouSpend = aggregate(
    context.records,
    "organizational_unit",
    "cost_usd",
  );
  const accountSpend = aggregate(context.records, "account_id", "cost_usd").map(
    (row) => ({ ...row, label: accountName(row.key) }),
  );
  const userSpend = aggregate(context.records, "user_id", "cost_usd");

  return `
    ${summaryStrip([
      [unique(context.records, "organizational_unit"), "organizational units"],
      [unique(context.records, "account_id"), "AWS accounts"],
      [unique(context.records, "user_id"), "identified users"],
      [context.modelAccess.length, "user-to-model relationships"],
    ])}
    <div class="visual-grid">
      ${panel({
        title: "Cost by organizational unit",
        subtitle: "Spend rolled up through AWS Organizations",
        tag: `${ouSpend.length} units`,
        span: 4,
        content: horizontalBars(ouSpend, {
          color: COLORS.purple,
          value: (row) => money(row.value, true),
        }),
      })}
      ${panel({
        title: "Cost by AWS account",
        subtitle: "Account allocation within the selected scope",
        tag: `${accountSpend.length} accounts`,
        span: 4,
        content: horizontalBars(accountSpend, {
          color: COLORS.blue,
          value: (row) => money(row.value, true),
        }),
      })}
      ${panel({
        title: "Cost by user",
        subtitle: "Usage attributed through CUR resource tags",
        tag: `${userSpend.length} users`,
        span: 4,
        content: horizontalBars(userSpend, {
          color: COLORS.teal,
          value: (row) => money(row.value, true),
        }),
      })}
      ${panel({
        title: "User-to-model access",
        subtitle: "Observed relationships from normalized invocation records",
        tag: `${context.modelAccess.length} relationships`,
        span: 12,
        content: accessTable(context.modelAccess),
      })}
    </div>
  `;
}

function renderBudgets(context) {
  const totalLimit = sum(context.budgets, "budget_limit_usd");
  const totalActual = sum(context.budgets, "actual_spend_usd");
  const totalForecast = sum(context.budgets, "forecasted_spend_usd");
  const exceeded = context.budgets.filter((budget) => budget.is_exceeded).length;

  return `
    <div class="kpi-grid">
      ${kpiCard({
        label: "Total budget limit",
        value: money(totalLimit),
        helper: `${context.budgets.length} account budgets`,
        signal: "Monthly",
        symbol: "◎",
        tone: "purple",
      })}
      ${kpiCard({
        label: "Actual spend",
        value: money(totalActual),
        helper: totalLimit ? `${formatPercent(totalActual / totalLimit)} of total limit` : "No budget selected",
        signal: exceeded ? `${exceeded} exceeded` : "Within limits",
        symbol: "$",
        tone: exceeded ? "red" : "blue",
      })}
      ${kpiCard({
        label: "Forecasted spend",
        value: money(totalForecast),
        helper: totalLimit ? `${formatPercent(totalForecast / totalLimit)} of total limit` : "No budget selected",
        signal: forecastRiskLabel(context.budgets),
        symbol: "↗",
        tone: totalForecast > totalLimit ? "orange" : "green",
      })}
    </div>
    <div class="visual-grid">
      ${panel({
        title: "Budget utilization",
        subtitle: "Limit, actual spend, and forecast by budget",
        tag: budgetHealthLabel(context.budgets),
        span: 12,
        legend: [
          ["Limit", COLORS.purple],
          ["Actual", COLORS.blue],
          ["Forecast", COLORS.orange],
        ],
        content: budgetComparison(context.budgets),
      })}
      ${panel({
        title: "Budget details",
        subtitle: "Account guardrails and current status",
        tag: `${exceeded} exceeded`,
        span: 12,
        content: budgetTable(context.budgets),
      })}
    </div>
  `;
}

function renderOptimization(context) {
  const totalSavings = sum(context.recommendations, "estimated_savings_usd");
  const byType = aggregate(
    context.recommendations,
    "recommendation_type",
    "estimated_savings_usd",
  ).map((row) => ({ ...row, label: splitIdentifier(row.key) }));
  const byAccount = aggregate(
    context.recommendations,
    "account_id",
    "estimated_savings_usd",
  ).map((row) => ({ ...row, label: accountName(row.key) }));

  return `
    <div class="kpi-grid">
      ${kpiCard({
        label: "Estimated savings",
        value: money(totalSavings),
        helper: `${context.recommendations.length} GenAI recommendations`,
        signal: "Prioritized",
        symbol: "↗",
        tone: "green",
      })}
    </div>
    <div class="visual-grid">
      ${panel({
        title: "Savings by recommendation type",
        subtitle: "Potential monthly savings grouped by action",
        tag: `${byType.length} action types`,
        span: 6,
        content: horizontalBars(byType, {
          color: COLORS.green,
          value: (row) => money(row.value),
        }),
      })}
      ${panel({
        title: "Savings by AWS account",
        subtitle: "Optimization opportunity by owning account",
        tag: `${byAccount.length} accounts`,
        span: 6,
        content: horizontalBars(byAccount, {
          color: COLORS.teal,
          value: (row) => money(row.value),
        }),
      })}
      ${panel({
        title: "Prioritized recommendations",
        subtitle: "Actionable Bedrock and SageMaker cost improvements",
        tag: "Cost Optimization Hub",
        span: 12,
        content: recommendationTable(context.recommendations),
      })}
    </div>
  `;
}

function renderDataQuality(context) {
  const quickCounts = filteredQuickCounts(context);
  const periodStart = state.data.period.start;
  const periodEnd = state.data.period.end;

  return `
    <div class="kpi-grid four-up">
      ${kpiCard({
        label: "Cost aggregation rows",
        value: numberFormatter.format(quickCounts.cost_aggregations),
        helper: "User, account, OU, and model totals",
        signal: "Ready",
        symbol: "Σ",
        tone: "purple",
      })}
      ${kpiCard({
        label: "Access relationships",
        value: numberFormatter.format(quickCounts.model_access),
        helper: "Distinct user-to-model pairs",
        signal: "Ready",
        symbol: "⑂",
        tone: "blue",
      })}
      ${kpiCard({
        label: "Budget records",
        value: numberFormatter.format(quickCounts.budgets),
        helper: "Limits, actuals, and forecasts",
        signal: "Ready",
        symbol: "$",
        tone: "orange",
      })}
      ${kpiCard({
        label: "Optimization records",
        value: numberFormatter.format(quickCounts.optimization_recommendations),
        helper: "Bedrock and SageMaker actions",
        signal: "Ready",
        symbol: "↗",
        tone: "green",
      })}
    </div>
    <div class="visual-grid">
      ${panel({
        title: "Cost dataset period coverage",
        subtitle: "Ingestion checks and Quick table coverage by analytical dimension",
        tag: "Validated",
        span: 12,
        content: `
          ${qualityFunnel(state.data.ingestion)}
          ${qualityCoverageTable(context, periodStart, periodEnd)}
        `,
      })}
    </div>
  `;
}

function kpiCard({
  label,
  value,
  helper,
  signal,
  symbol,
  tone = "purple",
}) {
  const palette = {
    purple: ["var(--purple)", "var(--purple-light)"],
    blue: ["var(--blue)", "var(--blue-light)"],
    teal: ["var(--teal)", "var(--teal-light)"],
    green: ["var(--green)", "var(--green-light)"],
    orange: ["var(--orange)", "var(--orange-light)"],
    red: ["var(--red)", "var(--red-light)"],
  }[tone];
  return `
    <article
      class="kpi-card"
      style="--kpi-color: ${palette[0]}; --kpi-soft: ${palette[1]}"
    >
      <div class="kpi-topline">
        <span class="kpi-label">${escapeHtml(label)}</span>
        <span class="kpi-symbol" aria-hidden="true">${escapeHtml(symbol)}</span>
      </div>
      <div class="kpi-value">${escapeHtml(String(value))}</div>
      <div class="kpi-footer">
        <span class="kpi-signal">${escapeHtml(signal)}</span>
        <span>${escapeHtml(helper)}</span>
      </div>
    </article>
  `;
}

function panel({
  title,
  subtitle,
  content,
  span = 6,
  tag = "",
  legend = [],
}) {
  const headerExtra = legend.length
    ? `<div class="chart-legend">${legend
        .map(
          ([label, color]) =>
            `<span><i class="legend-dot" style="--legend-color:${color}"></i>${escapeHtml(label)}</span>`,
        )
        .join("")}</div>`
    : tag
      ? `<span class="panel-tag">${escapeHtml(tag)}</span>`
      : "";
  return `
    <article class="panel span-${span}">
      <header class="panel-header">
        <div class="panel-heading">
          <h2>${escapeHtml(title)}</h2>
          <p>${escapeHtml(subtitle)}</p>
        </div>
        ${headerExtra}
      </header>
      ${content}
    </article>
  `;
}

function summaryStrip(items) {
  return `
    <div class="summary-strip">
      ${items
        .map(
          ([value, label]) =>
            `<div class="summary-chip"><strong>${escapeHtml(String(value))}</strong><span>${escapeHtml(label)}</span></div>`,
        )
        .join("")}
    </div>
  `;
}

function horizontalBars(
  rows,
  { color = COLORS.purple, value = (row) => row.value, limit = 9 } = {},
) {
  const displayed = rows
    .slice()
    .sort((left, right) => right.value - left.value)
    .slice(0, limit);
  if (!displayed.length) {
    return `<div class="empty-state">No data in the selected account scope.</div>`;
  }
  const maximum = Math.max(...displayed.map((row) => row.value), 0.000001);
  return `
    <div class="bar-chart">
      ${displayed
        .map((row) => {
          const width = (row.value / maximum) * 100;
          return `
            <div class="bar-row">
              <span class="bar-label" title="${escapeHtml(row.label || row.key)}">${escapeHtml(row.label || row.key)}</span>
              <span class="bar-track">
                <span
                  class="bar-fill"
                  style="--bar-width:${width.toFixed(2)}%;--bar-color:${color}"
                ></span>
              </span>
              <span class="bar-value">${escapeHtml(String(value(row)))}</span>
            </div>
          `;
        })
        .join("")}
    </div>
  `;
}

function lineChart(rows) {
  const sorted = rows.slice().sort((left, right) => left.key.localeCompare(right.key));
  if (!sorted.length) {
    return `<div class="empty-state">No trend data in the selected scope.</div>`;
  }

  const width = 720;
  const height = 220;
  const padding = { top: 20, right: 18, bottom: 30, left: 44 };
  const chartWidth = width - padding.left - padding.right;
  const chartHeight = height - padding.top - padding.bottom;
  const maximum = Math.max(...sorted.map((row) => row.value), 0.000001);
  const pointFor = (row, index) => {
    const x =
      padding.left +
      (sorted.length === 1 ? chartWidth / 2 : (index / (sorted.length - 1)) * chartWidth);
    const y = padding.top + chartHeight - (row.value / maximum) * chartHeight;
    return [x, y];
  };
  const points = sorted.map(pointFor);
  const polyline = points.map(([x, y]) => `${x},${y}`).join(" ");
  const area = [
    `${points[0][0]},${padding.top + chartHeight}`,
    ...points.map(([x, y]) => `${x},${y}`),
    `${points[points.length - 1][0]},${padding.top + chartHeight}`,
  ].join(" ");
  const labels = [0, Math.floor((sorted.length - 1) / 2), sorted.length - 1]
    .filter((value, index, values) => values.indexOf(value) === index)
    .map((index) => {
      const [x] = points[index];
      return `<text class="chart-axis-label" x="${x}" y="${height - 4}" text-anchor="middle">${escapeHtml(formatChartTime(sorted[index].key))}</text>`;
    })
    .join("");

  return `
    <div class="line-chart">
      <svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Spend trend line chart">
        <defs>
          <linearGradient id="trend-area" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stop-color="var(--purple)" stop-opacity=".28"></stop>
            <stop offset="100%" stop-color="var(--purple)" stop-opacity=".02"></stop>
          </linearGradient>
        </defs>
        ${[0, 0.5, 1]
          .map((ratio) => {
            const y = padding.top + ratio * chartHeight;
            const value = maximum * (1 - ratio);
            return `
              <line class="chart-grid-line" x1="${padding.left}" x2="${width - padding.right}" y1="${y}" y2="${y}"></line>
              <text class="chart-axis-label" x="${padding.left - 8}" y="${y + 3}" text-anchor="end">${escapeHtml(money(value, true))}</text>
            `;
          })
          .join("")}
        <polygon class="chart-area" points="${area}"></polygon>
        <polyline class="chart-line" points="${polyline}"></polyline>
        ${points
          .map(
            ([x, y], index) => `
              <circle class="chart-point" cx="${x}" cy="${y}" r="3.5">
                <title>${escapeHtml(formatChartTime(sorted[index].key))}: ${escapeHtml(money(sorted[index].value, true))}</title>
              </circle>
            `,
          )
          .join("")}
        ${labels}
      </svg>
    </div>
  `;
}

function tokenChart(rows) {
  const displayed = rows
    .slice()
    .sort(
      (left, right) =>
        right.input_tokens +
        right.output_tokens -
        (left.input_tokens + left.output_tokens),
    );
  if (!displayed.length) {
    return `<div class="empty-state">No token data in the selected scope.</div>`;
  }
  const maximum = Math.max(
    ...displayed.map((row) => row.input_tokens + row.output_tokens),
    1,
  );
  return `
    <div class="token-chart">
      ${displayed
        .map((row) => {
          const inputWidth = (row.input_tokens / maximum) * 100;
          const outputWidth = (row.output_tokens / maximum) * 100;
          const total = row.input_tokens + row.output_tokens;
          return `
            <div class="token-row">
              <span class="token-label" title="${escapeHtml(row.label)}">${escapeHtml(row.label)}</span>
              <span class="token-track" title="${numberFormatter.format(row.input_tokens)} input · ${numberFormatter.format(row.output_tokens)} output">
                <span class="token-input" style="--input-width:${inputWidth.toFixed(2)}%"></span>
                <span class="token-output" style="--output-width:${outputWidth.toFixed(2)}%"></span>
              </span>
              <span class="token-total">${compactFormatter.format(total)}</span>
            </div>
          `;
        })
        .join("")}
    </div>
  `;
}

function budgetComparison(budgets) {
  if (!budgets.length) {
    return `<div class="empty-state">No budgets in the selected account scope.</div>`;
  }
  return `
    <div class="budget-chart">
      ${budgets
        .slice()
        .sort((left, right) => right.actual_spend_usd - left.actual_spend_usd)
        .map((budget) => {
          const maximum = Math.max(
            budget.budget_limit_usd,
            budget.actual_spend_usd,
            budget.forecasted_spend_usd,
            1,
          );
          const limit = (budget.budget_limit_usd / maximum) * 100;
          const actual = (budget.actual_spend_usd / maximum) * 100;
          const forecast = (budget.forecasted_spend_usd / maximum) * 100;
          const utilization =
            budget.budget_limit_usd > 0
              ? budget.actual_spend_usd / budget.budget_limit_usd
              : 0;
          return `
            <div class="budget-row">
              <div class="budget-name">
                <strong title="${escapeHtml(budget.budget_name)}">${escapeHtml(budget.budget_name)}</strong>
                <span>${escapeHtml(accountName(budget.account_id))} · ${formatPercent(utilization)} used</span>
              </div>
              <div class="budget-bars">
                <div class="budget-track">
                  <span style="--budget-width:${limit.toFixed(2)}%;--budget-color:${COLORS.purple}"></span>
                </div>
                <div class="budget-track ${budget.actual_spend_usd > budget.budget_limit_usd ? "is-over" : ""}">
                  <span style="--budget-width:${actual.toFixed(2)}%;--budget-color:${COLORS.blue}"></span>
                </div>
                <div class="budget-track ${budget.forecasted_spend_usd > budget.budget_limit_usd ? "is-over" : ""}">
                  <span style="--budget-width:${forecast.toFixed(2)}%;--budget-color:${COLORS.orange}"></span>
                </div>
                <div class="budget-caption">
                  <span>Limit <strong>${money(budget.budget_limit_usd)}</strong></span>
                  <span>Actual <strong>${money(budget.actual_spend_usd)}</strong></span>
                  <span>Forecast <strong>${money(budget.forecasted_spend_usd)}</strong></span>
                </div>
              </div>
            </div>
          `;
        })
        .join("")}
    </div>
  `;
}

function modelTable(rows) {
  if (!rows.length) {
    return `<div class="empty-state">No model data in the selected scope.</div>`;
  }
  return table(
    ["Model or endpoint", "Service", "Cost", "Invocations", "Cost / invocation", "Input tokens", "Output tokens"],
    rows.map((row) => {
      const service = row.key.startsWith("genai-") ? "SageMaker" : "Bedrock";
      const unitCost = row.invocations ? row.cost_usd / row.invocations : 0;
      return [
        modelCell(row.key),
        `<span class="badge info">${service}</span>`,
        numeric(money(row.cost_usd, true)),
        numeric(numberFormatter.format(row.invocations)),
        numeric(money(unitCost, true)),
        numeric(numberFormatter.format(row.input_tokens)),
        numeric(numberFormatter.format(row.output_tokens)),
      ];
    }),
  );
}

function accessTable(relationships) {
  if (!relationships.length) {
    return `<div class="empty-state">No access relationships in the selected scope.</div>`;
  }
  return table(
    ["User", "Model or endpoint", "AWS account", "Relationship"],
    relationships
      .slice()
      .sort(
        (left, right) =>
          left.user_id.localeCompare(right.user_id) ||
          left.model_id.localeCompare(right.model_id),
      )
      .map((row) => [
        `<strong>${escapeHtml(row.user_id)}</strong>`,
        modelCell(row.model_id),
        escapeHtml(accountName(row.account_id)),
        `<span class="badge success">Observed</span>`,
      ]),
  );
}

function budgetTable(budgets) {
  if (!budgets.length) {
    return `<div class="empty-state">No budgets in the selected scope.</div>`;
  }
  return table(
    ["Budget", "AWS account", "Limit", "Actual", "Forecast", "Utilization", "Status"],
    budgets.map((budget) => {
      const utilization =
        budget.budget_limit_usd > 0
          ? budget.actual_spend_usd / budget.budget_limit_usd
          : 0;
      const forecastExceeded =
        budget.forecasted_spend_usd > budget.budget_limit_usd;
      return [
        `<strong>${escapeHtml(budget.budget_name)}</strong>`,
        escapeHtml(accountName(budget.account_id)),
        numeric(money(budget.budget_limit_usd)),
        numeric(money(budget.actual_spend_usd)),
        numeric(money(budget.forecasted_spend_usd)),
        numeric(formatPercent(utilization)),
        budget.is_exceeded
          ? `<span class="badge danger">Exceeded</span>`
          : forecastExceeded
            ? `<span class="badge warning">Forecast risk</span>`
            : `<span class="badge success">On track</span>`,
      ];
    }),
  );
}

function recommendationTable(recommendations) {
  if (!recommendations.length) {
    return `<div class="empty-state">No recommendations in the selected account scope.</div>`;
  }
  return table(
    ["Priority", "Recommendation", "AWS account", "Model or endpoint", "Description", "Savings"],
    recommendations
      .slice()
      .sort(
        (left, right) =>
          right.estimated_savings_usd - left.estimated_savings_usd,
      )
      .map((recommendation, index) => [
        `<span class="badge ${index < 2 ? "warning" : "info"}">${index < 2 ? "High" : "Medium"}</span>`,
        `<span class="recommendation-type">${escapeHtml(splitIdentifier(recommendation.recommendation_type))}</span>`,
        escapeHtml(accountName(recommendation.account_id)),
        recommendation.model_id
          ? modelCell(recommendation.model_id)
          : `<span class="badge">${escapeHtml(recommendation.service)}</span>`,
        escapeHtml(recommendation.description),
        numeric(`<strong>${money(recommendation.estimated_savings_usd)}</strong>`),
      ]),
  );
}

function qualityFunnel(ingestion) {
  const parsed =
    ingestion.raw_rows -
    ingestion.non_genai_filtered -
    ingestion.incomplete_skipped;
  return `
    <div class="quality-funnel" aria-label="Ingestion quality funnel">
      <div class="quality-step">
        <span>Raw CUR rows</span>
        <strong>${numberFormatter.format(ingestion.raw_rows)}</strong>
      </div>
      <div class="quality-step">
        <span>Valid GenAI rows</span>
        <strong>${numberFormatter.format(parsed)}</strong>
      </div>
      <div class="quality-step">
        <span>Duplicates removed</span>
        <strong>${numberFormatter.format(ingestion.duplicates_removed)}</strong>
      </div>
      <div class="quality-step">
        <span>Accepted records</span>
        <strong>${numberFormatter.format(ingestion.accepted_rows)}</strong>
      </div>
    </div>
  `;
}

function qualityCoverageTable(context, start, end) {
  const rows = [
    ["USER", unique(context.records, "user_id")],
    ["ACCOUNT", unique(context.records, "account_id")],
    ["ORGANIZATIONAL_UNIT", unique(context.records, "organizational_unit")],
    ["MODEL", unique(context.records, "model_id")],
  ];
  return table(
    ["Dimension type", "Period start", "Period end", "Distinct values", "Status"],
    rows.map(([dimension, count]) => [
      `<strong>${escapeHtml(dimension)}</strong>`,
      escapeHtml(formatDateTime(start)),
      escapeHtml(formatDateTime(end)),
      numeric(numberFormatter.format(count)),
      `<span class="badge success">Complete</span>`,
    ]),
  );
}

function table(headers, rows) {
  return `
    <div class="table-wrap">
      <table class="data-table">
        <thead>
          <tr>${headers.map((header) => `<th>${escapeHtml(header)}</th>`).join("")}</tr>
        </thead>
        <tbody>
          ${rows
            .map(
              (row) =>
                `<tr>${row.map((cell) => `<td>${cell}</td>`).join("")}</tr>`,
            )
            .join("")}
        </tbody>
      </table>
    </div>
  `;
}

function modelCell(modelId) {
  const label = modelLabel(modelId);
  return `
    <span class="model-cell">
      <span class="model-avatar" aria-hidden="true">${escapeHtml(label.slice(0, 2).toUpperCase())}</span>
      <strong title="${escapeHtml(modelId)}">${escapeHtml(label)}</strong>
    </span>
  `;
}

function numeric(content) {
  return `<span class="numeric">${content}</span>`;
}

function aggregate(rows, key, valueKey) {
  const totals = new Map();
  rows.forEach((row) => {
    totals.set(row[key], (totals.get(row[key]) || 0) + Number(row[valueKey] || 0));
  });
  return [...totals.entries()].map(([groupKey, value]) => ({
    key: String(groupKey),
    label: String(groupKey),
    value,
  }));
}

function aggregateMultiple(rows, key, valueKeys) {
  const totals = new Map();
  rows.forEach((row) => {
    if (!totals.has(row[key])) {
      totals.set(
        row[key],
        Object.fromEntries(valueKeys.map((valueKey) => [valueKey, 0])),
      );
    }
    const group = totals.get(row[key]);
    valueKeys.forEach((valueKey) => {
      group[valueKey] += Number(row[valueKey] || 0);
    });
  });
  return [...totals.entries()].map(([groupKey, values]) => ({
    key: String(groupKey),
    label: String(groupKey),
    ...values,
  }));
}

function withModelLabel(row) {
  return { ...row, label: modelLabel(row.key) };
}

function filteredQuickCounts(context) {
  const dimensions = [
    "user_id",
    "account_id",
    "organizational_unit",
    "model_id",
  ];
  return {
    cost_aggregations: dimensions.reduce(
      (total, dimension) => total + unique(context.records, dimension),
      0,
    ),
    model_access: context.modelAccess.length,
    budgets: context.budgets.length,
    optimization_recommendations: context.recommendations.length,
  };
}

function unique(rows, key) {
  return new Set(rows.map((row) => row[key])).size;
}

function sum(rows, key) {
  return rows.reduce((total, row) => total + Number(row[key] || 0), 0);
}

function accountName(accountId) {
  const account = state.data.accounts.find(
    (candidate) => candidate.account_id === accountId,
  );
  return account ? account.account_name : accountId;
}

function modelLabel(modelId) {
  return MODEL_LABELS[modelId] || splitIdentifier(modelId);
}

function splitIdentifier(value) {
  return String(value)
    .replace(/([a-z])([A-Z])/g, "$1 $2")
    .replace(/[._-]+/g, " ")
    .replace(/\b\w/g, (character) => character.toUpperCase());
}

function money(value, precise = false) {
  return (precise ? preciseCurrencyFormatter : currencyFormatter).format(
    Number(value || 0),
  );
}

function formatPercent(value) {
  return `${Math.round(Number(value || 0) * 100)}%`;
}

function formatDateRange(start, end) {
  const startDate = new Date(start);
  const endDate = new Date(end);
  const formatter = new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    timeZone: "UTC",
  });
  return `${formatter.format(startDate)} – ${formatter.format(endDate)} UTC`;
}

function formatDateTime(value) {
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    timeZone: "UTC",
  }).format(new Date(value));
}

function formatChartTime(value) {
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    timeZone: "UTC",
  }).format(new Date(value));
}

function budgetHealthLabel(budgets) {
  if (!budgets.length) {
    return "No budgets";
  }
  const exceeded = budgets.filter((budget) => budget.is_exceeded).length;
  return exceeded ? `${exceeded} exceeded` : "All on track";
}

function forecastRiskLabel(budgets) {
  const risk = budgets.filter(
    (budget) => budget.forecasted_spend_usd > budget.budget_limit_usd,
  ).length;
  return risk ? `${risk} forecast over` : "On track";
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}
