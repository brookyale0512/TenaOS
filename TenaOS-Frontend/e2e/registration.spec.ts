import { expect, test } from "@playwright/test";

test.beforeEach(async ({ page }) => {
  const username = process.env.OPENMRS_TEST_USERNAME ?? "admin";
  const password = process.env.OPENMRS_TEST_PASSWORD ?? "Admin123";
  await page.goto("/login");
  await page.getByLabel("Username").fill(username);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: /Sign in/i }).click();
  await expect(page).toHaveURL("/");
});

test("registers a patient, opens the chart, starts and ends a visit", async ({ page }) => {
  await page.goto("/patients/register");

  const lastName = `E2E-${Date.now()}`;
  await page.getByPlaceholder("Given name").fill("Playwright");
  await page.getByPlaceholder("Family name").fill(lastName);
  await page.getByRole("combobox", { name: /Gender/i }).click();
  await page.getByRole("option", { name: "Female" }).click();
  await page.locator('input[type="date"]').fill("1990-12-10");

  await page.getByRole("button", { name: /Next/i }).click();
  await page.getByRole("button", { name: /Generate/i }).click();
  await expect(page.getByText(/^Generated$/)).toBeVisible();

  await page.getByRole("button", { name: /Next/i }).click();
  await page.getByRole("combobox", { name: /Registration Location/i }).click();
  await page.getByRole("option").first().click();
  await page.getByRole("button", { name: /Next/i }).click();
  await page.getByRole("button", { name: /Next/i }).click();
  await page.getByRole("button", { name: /Register Patient/i }).click();

  // Lands on the patient chart.
  await expect(page.getByText(`Playwright ${lastName}`)).toBeVisible();

  // Start a visit.
  await page.getByRole("button", { name: /Start Visit/i }).click();
  await page.getByRole("combobox", { name: /Visit Type/i }).click();
  await page.getByRole("option").first().click();
  await page.getByRole("combobox", { name: /Location/i }).click();
  await page.getByRole("option").first().click();
  await page.getByRole("button", { name: /Start Visit/i }).last().click();

  // End the visit through the confirmation dialog.
  await page.getByRole("button", { name: /End Visit/i }).click();
  await page.getByRole("button", { name: /End visit/i }).click();
  await expect(page.getByRole("button", { name: /End Visit/i })).toHaveCount(0);
});
