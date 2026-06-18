import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { TranscriptEditor } from "../features/viewpoint/TranscriptEditor";
import type { ViewpointSegment } from "../api/types";
import { api } from "../api/client";

vi.mock("../api/client", () => ({
  api: {
    editSegmentText: vi.fn().mockResolvedValue({ segment_id: "seg_1", text: "改过的" }),
    labelSegments: vi.fn().mockResolvedValue({ labeled: 1 }),
    people: vi.fn().mockResolvedValue({
      people: [
        { person_id: "per_a", display_name: "韩文巧", person_type: "contact", is_self: 0, enrolled: true, attributed_count: 1, manual_count: 1 }
      ]
    }),
    audioUrl: (id: string) => `/api/audio/segments/${id}`
  }
}));

const segments: ViewpointSegment[] = [
  { segment_id: "seg_1", text: "你好", speaker: "spk_1", person_label: null },
  { segment_id: "seg_2", text: "我们开始吧", speaker: "spk_2", person_label: "韩文巧" }
];

afterEach(() => vi.clearAllMocks());

describe("TranscriptEditor", () => {
  it("renders each turn's text and resolved speaker label", () => {
    render(<TranscriptEditor segments={segments} stale={false} onChanged={vi.fn()} />);
    expect(screen.getByText("你好")).toBeInTheDocument();
    expect(screen.getByText("我们开始吧")).toBeInTheDocument();
    // person_label wins over the raw speaker when present.
    expect(screen.getByText("韩文巧")).toBeInTheDocument();
    expect(screen.getByText("spk_1")).toBeInTheDocument();
  });

  it("shows the stale banner only when stale", () => {
    const { rerender } = render(<TranscriptEditor segments={segments} stale={false} onChanged={vi.fn()} />);
    expect(screen.queryByText(/转写已改动/)).not.toBeInTheDocument();
    rerender(<TranscriptEditor segments={segments} stale onChanged={vi.fn()} />);
    expect(screen.getByText(/转写已改动/)).toBeInTheDocument();
  });

  it("edits a turn's text -> editSegmentText then onChanged refetch", async () => {
    const onChanged = vi.fn();
    render(<TranscriptEditor segments={segments} stale={false} onChanged={onChanged} />);

    // Open the inline editor for the first turn.
    await userEvent.click(screen.getAllByRole("button", { name: /编辑/ })[0]);
    const box = screen.getByRole("textbox");
    await userEvent.clear(box);
    await userEvent.type(box, "改过的文本");
    await userEvent.click(screen.getByRole("button", { name: /保存/ }));

    await waitFor(() => expect(api.editSegmentText).toHaveBeenCalledWith("seg_1", "改过的文本"));
    expect(onChanged).toHaveBeenCalled();
  });

  it("reassigning a speaker labels the segment to a person then refetches", async () => {
    const onChanged = vi.fn();
    render(<TranscriptEditor segments={segments} stale={false} onChanged={onChanged} />);

    // The people roster loads async; wait for its option before selecting it.
    await screen.findAllByRole("option", { name: "韩文巧" });
    const select = screen.getAllByRole("combobox")[0];
    await userEvent.selectOptions(select, "per_a");

    await waitFor(() => expect(api.labelSegments).toHaveBeenCalledWith("per_a", ["seg_1"]));
    expect(onChanged).toHaveBeenCalled();
  });

  it("clicking a turn plays its segment audio", async () => {
    render(<TranscriptEditor segments={segments} stale={false} onChanged={vi.fn()} />);
    // The play control exists per turn (audio is best-effort; we only assert it's wired/clickable).
    const play = screen.getAllByRole("button", { name: /播放/ });
    expect(play.length).toBe(2);
    await userEvent.click(play[0]);
  });
});
