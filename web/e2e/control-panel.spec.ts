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

test("管道导入 → 运行 → 审核(日期/会话 → 接受 → 指派发言人)→ 总结", async ({ page }) => {
  await page.goto(PANEL);

  // 0. The app opens on 今日; the DevicePanel lives on the 管道 tab (sidebar).
  await page.getByRole("tab", { name: "管道" }).click();

  // 1. Device-first import: click 「导入」 on the first device card. No typed path.
  await page.locator(".device-card").first().getByRole("button", { name: "导入" }).click();

  // 2. The background worker is alive — the glass status-bar pill goes live.
  //    (Mock backends complete fast; SSE drives the status.)
  await expect(page.locator(".statusbar-pill.live")).toBeVisible({ timeout: 30_000 });

  // 3. Navigate 审核 → 按天浏览 → 日期 → 会话. Day buttons also exist in the
  //    sidebar 资料库, so scope to the day rail (same as the unit tests).
  await page.getByRole("tab", { name: "审核" }).click();
  await page.getByRole("tab", { name: "按天浏览" }).click();
  const rail = page.getByRole("navigation", { name: "日期与会话" });
  await rail.getByRole("button", { name: /^2087-/ }).first().click();
  await rail.getByRole("button", { name: /^ses_/ }).first().click();

  // 4. Accept a transcript segment. Match the per-segment 「接受」 button exactly
  //    so it does not collide with 「全部接受剩余」 or the 接受 status chip.
  await page.getByRole("button", { name: "接受", exact: true }).first().click();

  // 5. Assign a speaker to a person via the 「指派发言人 …」 select.
  await page.getByLabel(/^指派发言人 /).first().selectOption({ index: 1 });

  // 6. 总结 tab: the per-session workspace is the default view (mode toggle visible).
  await page.getByRole("tab", { name: "总结" }).click();
  await expect(page.getByRole("tab", { name: "会话总结" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "日报汇总" })).toBeVisible();
});
