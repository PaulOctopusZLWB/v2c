import { describe, expect, it } from "vitest";
import { groupIntoTurns } from "../lib/turns";
import type { TranscriptSegment } from "../api/types";

function seg(
  id: string,
  speaker: string,
  start: string,
  end: string,
  person_id: string | null = null,
  person_label: string | null = null
): TranscriptSegment {
  return {
    segment_id: id,
    text: `text ${id}`,
    speaker,
    start_ms: 0,
    end_ms: 1000,
    absolute_start_at: start,
    absolute_end_at: end,
    review_status: "pending_review",
    note: null,
    person_id,
    person_label
  };
}

describe("groupIntoTurns", () => {
  it("merges consecutive same-speaker segments into turns", () => {
    const a = seg("A", "spkA", "2026-06-13T09:00:00+08:00", "2026-06-13T09:00:01+08:00");
    const b = seg("B", "spkA", "2026-06-13T09:00:02+08:00", "2026-06-13T09:00:03+08:00");
    const c = seg("C", "spkB", "2026-06-13T09:00:04+08:00", "2026-06-13T09:00:05+08:00");
    const d = seg("D", "spkA", "2026-06-13T09:00:06+08:00", "2026-06-13T09:00:07+08:00");

    const turns = groupIntoTurns([a, b, c, d]);

    expect(turns).toHaveLength(3);

    expect(turns[0].speaker).toBe("spkA");
    expect(turns[0].label).toBe("spkA"); // unattributed → raw spk label
    expect(turns[0].personId).toBeNull();
    expect(turns[0].segments).toEqual([a, b]);
    expect(turns[0].segment_ids).toEqual(["A", "B"]);
    expect(turns[0].start).toBe("2026-06-13T09:00:00+08:00");
    expect(turns[0].end).toBe("2026-06-13T09:00:03+08:00"); // b's end

    expect(turns[1].speaker).toBe("spkB");
    expect(turns[1].segments).toEqual([c]);
    expect(turns[1].segment_ids).toEqual(["C"]);
    expect(turns[1].start).toBe("2026-06-13T09:00:04+08:00");
    expect(turns[1].end).toBe("2026-06-13T09:00:05+08:00");

    expect(turns[2].speaker).toBe("spkA");
    expect(turns[2].segments).toEqual([d]);
    expect(turns[2].segment_ids).toEqual(["D"]);
    expect(turns[2].start).toBe("2026-06-13T09:00:06+08:00");
    expect(turns[2].end).toBe("2026-06-13T09:00:07+08:00");
  });

  it("groups by resolved person identity, not by spk label", () => {
    // A & B are the same person (韩) even though the diarizer split them across spk_1/spk_2;
    // C is the same spk_1 as A but has no attribution → its own unattributed turn.
    const a = seg("A", "spk_1", "2026-06-13T09:00:00+08:00", "2026-06-13T09:00:01+08:00", "per_han", "韩");
    const b = seg("B", "spk_2", "2026-06-13T09:00:02+08:00", "2026-06-13T09:00:03+08:00", "per_han", "韩");
    const c = seg("C", "spk_1", "2026-06-13T09:00:04+08:00", "2026-06-13T09:00:05+08:00");

    const turns = groupIntoTurns([a, b, c]);

    expect(turns).toHaveLength(2);

    // First turn merges A+B under the person 韩 (person_id matches across different spk labels).
    expect(turns[0].label).toBe("韩");
    expect(turns[0].personId).toBe("per_han");
    expect(turns[0].speaker).toBe("spk_1"); // first segment's raw speaker preserved
    expect(turns[0].segment_ids).toEqual(["A", "B"]);
    expect(turns[0].start).toBe("2026-06-13T09:00:00+08:00");
    expect(turns[0].end).toBe("2026-06-13T09:00:03+08:00");

    // Second turn is C, unattributed → labelled by its raw spk_1.
    expect(turns[1].label).toBe("spk_1");
    expect(turns[1].personId).toBeNull();
    expect(turns[1].speaker).toBe("spk_1");
    expect(turns[1].segment_ids).toEqual(["C"]);
  });

  it("returns [] for no segments", () => {
    expect(groupIntoTurns([])).toEqual([]);
  });
});
