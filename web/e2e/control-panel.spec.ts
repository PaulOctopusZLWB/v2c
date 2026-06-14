import { expect, test } from "@playwright/test";

const PANEL = "http://127.0.0.1:5173";
const SAMPLE_DIR = process.env.PCN_E2E_SOURCE_DIR ?? "sample_data/e2e";

test("import -> run -> status -> transcript accept -> speaker assign -> llm result", async ({ page }) => {
  await page.goto(PANEL);

  // 1. Import + run.
  await page.getByLabel("Source directory").fill(SAMPLE_DIR);
  await page.getByRole("button", { name: "Import" }).click();

  // 2. SSE-driven status reaches a succeeded task (mock backends complete fast).
  await expect(page.locator(".task-row", { hasText: "succeeded" }).first()).toBeVisible({ timeout: 30_000 });

  // 3. Navigate by day -> session (the chosen navigation model). Selecting a day also loads its LLM result.
  await page.getByRole("button", { name: /^2087-/ }).first().click();
  await page.getByRole("button", { name: /^ses_/ }).first().click();

  // 4. Accept a transcript segment in the loaded session.
  await page.getByRole("button", { name: "Accept" }).first().click();

  // 5. Speaker -> person assignment.
  await page.getByLabel(/^Person for /).first().selectOption({ index: 1 });

  // 6. Read-only LLM result is visible with the Obsidian pointer.
  await expect(page.getByText(/Memory candidates \(read-only\)/)).toBeVisible();
  await expect(page.getByText(/Confirm or reject these in Obsidian/)).toBeVisible();
});
