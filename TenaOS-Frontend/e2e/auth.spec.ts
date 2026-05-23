import { expect, test } from "@playwright/test";

test.describe("authentication", () => {
  test("anonymous visitor is redirected to /login", async ({ page }) => {
    await page.goto("/");
    await expect(page).toHaveURL(/\/login$/);
    await expect(page.getByRole("heading", { name: /Sign in/ })).toBeVisible();
  });

  test("invalid credentials surface a sign-in error message", async ({ page }) => {
    await page.goto("/login");
    await page.getByLabel("Username").fill("not-a-user");
    await page.getByLabel("Password").fill("wrong-password");
    await page.getByRole("button", { name: /Sign in/i }).click();
    await expect(page.getByText(/Sign-in failed/i)).toBeVisible();
  });

  test("valid credentials land on the dashboard", async ({ page }) => {
    const username = process.env.OPENMRS_TEST_USERNAME ?? "admin";
    const password = process.env.OPENMRS_TEST_PASSWORD ?? "Admin123";
    await page.goto("/login");
    await page.getByLabel("Username").fill(username);
    await page.getByLabel("Password").fill(password);
    await page.getByRole("button", { name: /Sign in/i }).click();
    await expect(page).toHaveURL("/");
    await expect(page.getByRole("link", { name: /Patients/i }).first()).toBeVisible();
  });
});
