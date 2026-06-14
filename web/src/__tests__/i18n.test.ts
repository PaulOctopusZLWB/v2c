import { describe, expect, it } from "vitest";
import { t, STAGE_LABELS } from "../i18n";

describe("i18n glossary", () => {
  it("exposes the six Chinese stage labels in order", () => {
    expect(STAGE_LABELS).toEqual(["设备", "导入", "转写", "审核", "观点", "发布"]);
  });
  it("maps review statuses to Chinese", () => {
    expect(t.review.accepted).toBe("接受");
    expect(t.review.rejected).toBe("拒绝");
    expect(t.review.needs_fix).toBe("存疑");
    expect(t.review.pending_review).toBe("待审");
  });
});
