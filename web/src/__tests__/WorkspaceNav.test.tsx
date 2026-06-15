import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { WorkspaceNav } from "../features/workspace/WorkspaceNav";

describe("WorkspaceNav", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      if (url === "/api/transcripts/days") return new Response(JSON.stringify({ days: [{ day: "2087-05-10", session_count: 2 }] }), { status: 200 });
      if (url === "/api/transcripts/days/2087-05-10/sessions")
        return new Response(JSON.stringify({ day: "2087-05-10", sessions: [{ session_id: "ses_1", started_at: "", segment_count: 3, review_status: "pending_review" }] }), { status: 200 });
      return new Response("{}", { status: 200 });
    }));
  });
  afterEach(() => vi.unstubAllGlobals());

  it("lists days, then lists sessions for the selected day", async () => {
    const onSelectDay = vi.fn();
    const onSelectSession = vi.fn();
    const days = [{ day: "2087-05-10", session_count: 2 }];
    const { rerender } = render(<WorkspaceNav days={days} selectedDay={null} onSelectDay={onSelectDay} onSelectSession={onSelectSession} />);

    await waitFor(() => expect(screen.getByRole("button", { name: /2087-05-10/ })).toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: /2087-05-10/ }));
    expect(onSelectDay).toHaveBeenCalledWith("2087-05-10");

    rerender(<WorkspaceNav days={days} selectedDay="2087-05-10" onSelectDay={onSelectDay} onSelectSession={onSelectSession} />);
    await waitFor(() => expect(screen.getByRole("button", { name: /ses_1/ })).toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: /ses_1/ }));
    expect(onSelectSession).toHaveBeenCalledWith("ses_1");
  });
});
