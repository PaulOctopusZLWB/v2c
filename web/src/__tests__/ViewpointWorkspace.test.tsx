import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ViewpointWorkspace } from "../features/viewpoint/ViewpointWorkspace";
import type { ViewpointState } from "../api/types";
import { api } from "../api/client";

vi.mock("../api/client", () => ({
  api: {
    days: vi.fn().mockResolvedValue({ days: [{ day: "2087-05-10", session_count: 1 }] }),
    sessionsForDay: vi.fn().mockResolvedValue({
      day: "2087-05-10",
      sessions: [{ session_id: "ses_1", started_at: "2087-05-10T09:30:00+08:00", segment_count: 3, review_status: "accepted", name: "晨会" }]
    }),
    viewpoint: vi.fn(),
    people: vi.fn().mockResolvedValue({ people: [] }),
    generateViewpoint: vi.fn().mockResolvedValue({ enqueued: true, session_id: "ses_1" }),
    editViewpoint: vi.fn(),
    editSegmentText: vi.fn(),
    labelSegments: vi.fn(),
    setSessionPromptOverride: vi.fn(),
    setSessionPrompt: vi.fn(),
    clearViewpointEdit: vi.fn(),
    publishViewpoint: vi.fn(),
    audioUrl: (id: string) => `/api/audio/segments/${id}`
  }
}));

function vpState(over: Partial<ViewpointState> = {}): ViewpointState {
  return {
    session_id: "ses_1",
    segments: [{ segment_id: "seg_1", text: "你好", speaker: "spk_1", person_label: null }],
    prompt: { effective: "请总结。", default: "请总结。", is_override: false },
    generated: null,
    edited: null,
    effective: null,
    status: "draft",
    stale: false,
    has_generated: false,
    generating: false,
    published_at: null,
    note_path: null,
    ...over
  };
}

afterEach(() => vi.clearAllMocks());

describe("ViewpointWorkspace", () => {
  it("picks a session and loads its viewpoint into the 2-pane workspace", async () => {
    (api.viewpoint as ReturnType<typeof vi.fn>).mockResolvedValue(vpState());
    render(<ViewpointWorkspace />);

    // Pick the day, then the session.
    await userEvent.selectOptions(await screen.findByLabelText(/日期/), "2087-05-10");
    await userEvent.selectOptions(await screen.findByLabelText(/会话/), "ses_1");

    await waitFor(() => expect(api.viewpoint).toHaveBeenCalledWith("ses_1"));
    // The transcript turn renders in the left pane.
    expect(await screen.findByText("你好")).toBeInTheDocument();
  });

  it("polls viewpoint while generating and stops once done", async () => {
    vi.useFakeTimers();
    try {
      const viewpoint = api.viewpoint as ReturnType<typeof vi.fn>;
      // First load: generating. Subsequent polls: still generating, then done.
      viewpoint
        .mockResolvedValueOnce(vpState({ generating: true }))
        .mockResolvedValueOnce(vpState({ generating: true }))
        .mockResolvedValue(vpState({ generating: false, has_generated: true, effective: {
          headline: "好", summary: "", topics: [], decisions: [], todos: [], open_questions: [], core_conclusions: [], per_speaker: []
        } }));

      render(<ViewpointWorkspace />);
      await act(async () => { await vi.advanceTimersByTimeAsync(0); }); // days load

      // Under fake timers, findBy*'s internal polling can't advance — query synchronously after
      // flushing microtasks (the day select exists once api.days resolved).
      const daySel = screen.getByLabelText("观点日期");
      await act(async () => { fireSelect(daySel, "2087-05-10"); await vi.advanceTimersByTimeAsync(0); });
      const sessSel = screen.getByLabelText("观点会话");
      await act(async () => { fireSelect(sessSel, "ses_1"); await vi.advanceTimersByTimeAsync(0); });

      // Initial viewpoint load returned generating:true -> polling starts.
      expect(viewpoint).toHaveBeenCalledWith("ses_1");
      const afterLoad = viewpoint.mock.calls.length;

      // Advance ~2s -> one poll.
      await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
      expect(viewpoint.mock.calls.length).toBeGreaterThan(afterLoad);

      // Advance again -> reaches the done state, polling stops.
      await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
      const afterDone = viewpoint.mock.calls.length;
      await act(async () => { await vi.advanceTimersByTimeAsync(4000); });
      // No further polls after generating flips false.
      expect(viewpoint.mock.calls.length).toBe(afterDone);
    } finally {
      vi.useRealTimers();
    }
  });
});

/** Set a <select> value and dispatch change (userEvent doesn't play well with fake timers). */
function fireSelect(el: HTMLElement, value: string) {
  (el as HTMLSelectElement).value = value;
  el.dispatchEvent(new Event("change", { bubbles: true }));
}
