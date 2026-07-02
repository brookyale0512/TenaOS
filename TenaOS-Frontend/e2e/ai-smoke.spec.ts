import { expect, type Page, type Route, test } from "@playwright/test";

const session = {
  authenticated: true,
  sessionId: "demo-session",
  locale: "en",
  user: {
    uuid: "demo-user",
    display: "Demo Admin",
    username: "admin",
    systemId: "admin",
    person: { uuid: "demo-person", display: "Demo Admin", preferredName: { display: "Demo" } },
    roles: [],
    privileges: [],
  },
  currentProvider: { uuid: "demo-provider", display: "Demo Provider" },
};

const health = {
  ok: true,
  ciel: { available: true, error: null, sqlitePath: "/tmp/ciel.db" },
  llm: { healthy: true, message: "ready", baseUrl: "http://llm.local", model: "gemma-4" },
};

const formDraft = {
  draftId: "form-draft-1",
  owner: null,
  status: "draft",
  name: "Demo triage form",
  version: "1.0",
  description: "Smoke test form",
  encounterTypeUuid: "encounter-type-1",
  basket: {
    sections: [
      {
        sectionId: "triage",
        label: "Triage",
        conceptId: null,
        kind: "container",
        isExpanded: true,
        fields: [{ conceptId: "5089", labelOverride: "Weight", required: true, renderingOverride: "number" }],
      },
    ],
  },
  lastSchema: null,
  lastValidation: null,
  publishedFormUuid: null,
  createdAt: "2026-05-25T00:00:00Z",
  updatedAt: "2026-05-25T00:00:00Z",
  conversationState: "awaiting_question",
  conversationContext: {},
};

const reportSpec = {
  reportType: "count",
  dateFrom: null,
  dateTo: null,
  dateRangeLabel: null,
  filters: [
    {
      filterId: "filter-1",
      conceptId: "1490",
      label: "Cough",
      filterMode: "any_value",
      valueConceptId: null,
      valueBool: null,
      operator: null,
      numericThreshold: null,
    },
  ],
  joinMode: "and",
  denominator: null,
  groupBy: [],
  visualization: null,
};

const reportDraft = {
  draftId: "report-draft-1",
  owner: null,
  status: "draft",
  name: "Demo cough count",
  description: "Smoke test report",
  published: false,
  reportType: "count",
  spec: reportSpec,
  lastQuery: null,
  lastResult: null,
  lastRunAt: null,
  createdAt: "2026-05-25T00:00:00Z",
  updatedAt: "2026-05-25T00:00:00Z",
  conversationState: "ready",
  conversationContext: {},
};

async function fulfillJson(route: Route, body: unknown) {
  await route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

async function mockDemoShell(page: Page) {
  await page.route("**/openmrs/ws/rest/v1/session**", async (route) => fulfillJson(route, session));
  await page.route("**/openmrs/ws/rest/v1/user/demo-user**", async (route) =>
    fulfillJson(route, { uuid: "demo-user", userProperties: { defaultLocation: "loc-1" } }),
  );
  await page.route("**/openmrs/ws/rest/v1/location**", async (route) =>
    fulfillJson(route, { results: [{ uuid: "loc-1", display: "Demo Clinic", retired: false }] }),
  );
  await page.route("**/agent-api/health", async (route) => fulfillJson(route, health));
  await page.route(/\/agent-api\/reports\/drafts\?published=true/, async (route) => fulfillJson(route, { drafts: [] }));
}

async function mockFormBuilder(page: Page) {
  await page.route("**/agent-api/forms/drafts", async (route) => {
    if (route.request().method() === "POST") {
      await fulfillJson(route, formDraft);
      return;
    }
    await fulfillJson(route, { drafts: [formDraft] });
  });
  await page.route("**/agent-api/forms/drafts/form-draft-1", async (route) => fulfillJson(route, formDraft));
  await page.route("**/agent-api/forms/drafts/form-draft-1/schema", async (route) =>
    fulfillJson(route, { schema: null, validation: { issues: [] } }),
  );
  await page.route("**/agent-api/forms/drafts/form-draft-1/events", async (route) =>
    fulfillJson(route, { events: [] }),
  );
}

async function mockReportBuilder(page: Page) {
  await page.route("**/agent-api/reports/drafts", async (route) => {
    if (route.request().method() === "POST") {
      await fulfillJson(route, reportDraft);
      return;
    }
    await fulfillJson(route, { drafts: [reportDraft] });
  });
  await page.route("**/agent-api/reports/drafts/report-draft-1", async (route) => fulfillJson(route, reportDraft));
  await page.route("**/agent-api/reports/drafts/report-draft-1/result", async (route) =>
    fulfillJson(route, { result: null, lastRunAt: null, status: "draft" }),
  );
  await page.route("**/agent-api/reports/drafts/report-draft-1/events", async (route) =>
    fulfillJson(route, { events: [] }),
  );
}

async function mockLabCatalog(page: Page) {
  const entry = {
    uuid: "lab-1",
    conceptId: "21",
    conceptUuid: "concept-21",
    displayName: "Hemoglobin",
    category: "Hematology",
    units: "g/dL",
    lowNormal: 12,
    hiNormal: 16,
    lowCritical: null,
    hiCritical: null,
    order: 1,
    addedAt: "2026-05-25T00:00:00Z",
  };
  await page.route("**/agent-api/labs/catalog", async (route) =>
    fulfillJson(route, { catalog: { Hematology: [entry] } }),
  );
  await page.route("**/agent-api/labs/catalog/add", async (route) =>
    fulfillJson(route, { status: "added", entry, interpreted: "Hemoglobin" }),
  );
}

test.describe("AI feature smoke tests", () => {
  test.beforeEach(async ({ page }) => {
    await mockDemoShell(page);
  });

  test("opens the form builder workspace with healthy agent dependencies", async ({ page }) => {
    await mockFormBuilder(page);

    await page.goto("/forms/new");

    await expect(page.getByText("Form Builder Assistant")).toBeVisible();
    await expect(page.getByRole("heading", { name: "Demo triage form" })).toBeVisible();
  });

  test("opens the report builder workspace with healthy agent dependencies", async ({ page }) => {
    await mockReportBuilder(page);

    await page.goto("/reports/new");

    await expect(page.getByText("Report Builder Assistant")).toBeVisible();
    await expect(page.getByRole("heading", { name: "Demo cough count" })).toBeVisible();
  });

  test("adds a lab test through the lab assistant without rendering raw HTML", async ({ page }) => {
    await mockLabCatalog(page);

    await page.goto("/labs/manage");
    await page.getByPlaceholder(/Type a lab test name/i).fill("haemoglobin");
    await page.getByPlaceholder(/Type a lab test name/i).press("Enter");

    await expect(page.getByText("Hemoglobin", { exact: true })).toBeVisible();
    await expect(page.getByText(/Added Hemoglobin to Hematology/)).toBeVisible();
  });
});
