import { expect, test } from "@playwright/test";

// Full-chain control-room e2e (Chinese UI, device-first import).
//
// This spec is GATED behind a live stack — it is NOT run by `npm test`
// (vitest only globs src/**) and there is no Playwright config that starts a
// server for it. To run it manually you need the real backend + built panel:
//
//   # backend (serves /api + the built SPA under /app)
//   UV_CACHE_DIR=.tmp/uv-cache uv run pcn web \
//     --data-dir .tmp/acceptance/data \
//     --obsidian-vault .tmp/acceptance/vault --port 8765
//   # dev panel (proxies /api -> :8765), then:
//   cd web && npm run dev
//   PCN_E2E_SOURCE_DIR=sample_data/e2e npx playwright test e2e/control-panel.spec.ts
//
// It expects at least one importable source to show as a device card.

const PANEL = "http://127.0.0.1:5173";

test("设备导入 → 运行 → 日期/会话 → 接受 → 指派发言人 → 观点(只读)", async ({ page }) => {
  await page.goto(PANEL);

  // 1. Device-first import: click 「导入」 on the first device card. No typed path.
  await page.locator(".device-card").first().getByRole("button", { name: "导入" }).click();

  // 2. The background worker is alive — the header live indicator reads 运行中.
  //    (Mock backends complete fast; SSE drives the status.)
  await expect(page.locator(".workbench-header .live")).toContainText("运行中", { timeout: 30_000 });

  // 3. Navigate 日期 → 会话. Buttons are keyed by id, so language-neutral:
  //    a day button (YYYY-MM-DD …) then a session button (ses_… · status).
  await page.getByRole("button", { name: /^2087-/ }).first().click();
  await page.getByRole("button", { name: /^ses_/ }).first().click();

  // 4. Accept a transcript segment. Match the per-segment 「接受」 button exactly
  //    so it does not collide with 「全部接受剩余」 or the 接受 status chip.
  await page.getByRole("button", { name: "接受", exact: true }).first().click();

  // 5. Assign a speaker to a person via the 「指派发言人 …」 select.
  await page.getByLabel(/^指派发言人 /).first().selectOption({ index: 1 });

  // 6. Read-only viewpoint panel: shows 观点 and the Obsidian-only confirm pointer.
  await expect(page.locator(".llm-panel").getByRole("heading", { name: /观点/ })).toBeVisible();
  await expect(page.getByText(/确认\/拒绝请在 Obsidian 完成/)).toBeVisible();
});
