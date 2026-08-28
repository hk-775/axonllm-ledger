#!/usr/bin/env node

import fs from "node:fs";
import vm from "node:vm";

const appSource = fs.readFileSync("site/app.js", "utf8");
const dashboardData = JSON.parse(
  fs.readFileSync("site/data/dashboard.json", "utf8"),
);

const sandbox = {
  console,
  dashboardData,
  URL,
  URLSearchParams,
  document: {
    addEventListener() {},
  },
  window: {
    location: {
      href: "https://example.test/axonllm-ledger/",
      search: "",
    },
    localStorage: {
      getItem() {
        return null;
      },
      setItem() {},
    },
    matchMedia() {
      return { matches: false };
    },
  },
};

vm.createContext(sandbox);
vm.runInContext(appSource, sandbox, { filename: "site/app.js" });

const results = vm.runInContext(
  `
    state.data = dashboardData;
    const output = [];
    const accountScopes = [
      "all",
      ...dashboardData.accounts.map((account) => account.account_id),
    ];

    for (const accountId of accountScopes) {
      state.accountId = accountId;
      for (const [sheetId, definition] of Object.entries(SHEETS)) {
        state.sheet = sheetId;
        const html = definition.render(getContext());
        output.push({
          accountId,
          sheetId,
          html,
          kpis: (html.match(/class="kpi-card"/g) || []).length,
          panels: (html.match(/class="panel span-/g) || []).length,
        });
      }
    }
    output;
  `,
  sandbox,
);

for (const result of results) {
  if (result.html.length < 500) {
    throw new Error(
      `${result.sheetId} rendered unexpectedly little content for ${result.accountId}`,
    );
  }
  for (const invalidValue of ["undefined", "NaN", "[object Object]"]) {
    if (result.html.includes(invalidValue)) {
      throw new Error(
        `${result.sheetId} rendered ${invalidValue} for ${result.accountId}`,
      );
    }
  }
}

const allAccounts = results.filter((result) => result.accountId === "all");
const visualCount = allAccounts.reduce(
  (total, result) => total + result.kpis + result.panels,
  0,
);
if (visualCount !== dashboardData.project.visuals) {
  throw new Error(
    `rendered ${visualCount} visuals; expected ${dashboardData.project.visuals}`,
  );
}

console.log(
  `Validated ${results.length} dashboard renders across ` +
    `${dashboardData.accounts.length + 1} account scopes; ` +
    `${visualCount} visuals rendered for the portfolio view.`,
);
