import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ScopeSelector } from "../features/viz/ScopeSelector";

/** Mock api.days() + api.sessionsForDay(day) over fetch (the client builds the URLs). */
function mockFetch() {
  return vi.fn(async (url: string) => {
    const path = String(url).split("?")[0];
    if (path === "/api/transcripts/days")
      return new Response(
        JSON.stringify({
          days: [
            { day: "2026-06-15", session_count: 2 },
            { day: "2026-06-14", session_count: 1 }
          ]
        }),
        { status: 200 }
      );
    if (path === "/api/transcripts/days/2026-06-15/sessions")
      return new Response(
        JSON.stringify({
          day: "2026-06-15",
          sessions: [
            { session_id: "ses_a", started_at: "2026-06-15T09:30:00+08:00", segment_count: 12, review_status: "pending_review" },
            { session_id: "ses_b", started_at: "2026-06-15T14:05:00+08:00", segment_count: 7, review_status: "accepted" }
          ]
        }),
        { status: 200 }
      );
    return new Response("{}", { status: 200 });
  });
}

const EMPTY = { session_ids: [], days: [] };

describe("ScopeSelector", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", mockFetch());
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("renders the days from api.days()", async () => {
    render(<ScopeSelector value={EMPTY} onChange={vi.fn()} />);
    expect(await screen.findByText(/2026-06-15/)).toBeInTheDocument();
    expect(screen.getByText(/2026-06-14/)).toBeInTheDocument();
  });

  it("checking a day calls onChange with that day in `days`", async () => {
    const onChange = vi.fn();
    render(<ScopeSelector value={EMPTY} onChange={onChange} />);
    await screen.findByText(/2026-06-15/);

    await userEvent.click(screen.getByRole("checkbox", { name: /2026-06-15/ }));

    expect(onChange).toHaveBeenCalledWith({ session_ids: [], days: ["2026-06-15"] });
  });

  it("expanding a day lists its sessions and checking one adds its id to `session_ids`", async () => {
    const onChange = vi.fn();
    render(<ScopeSelector value={EMPTY} onChange={onChange} />);
    await screen.findByText(/2026-06-15/);

    // Expand the day to load + reveal its sessions.
    await userEvent.click(screen.getByRole("button", { name: /展开 2026-06-15/ }));

    // The session rows show time + segment count.
    await waitFor(() => expect(screen.getByText(/09:30/)).toBeInTheDocument());
    expect(screen.getByText(/14:05/)).toBeInTheDocument();

    // Check the first session → its id flows up into session_ids.
    await userEvent.click(screen.getByRole("checkbox", { name: /09:30/ }));
    expect(onChange).toHaveBeenCalledWith({ session_ids: ["ses_a"], days: [] });
  });

  it("清空 resets the selection", async () => {
    const onChange = vi.fn();
    render(<ScopeSelector value={{ session_ids: ["ses_a"], days: ["2026-06-15"] }} onChange={onChange} />);
    await screen.findByText(/2026-06-15/);

    await userEvent.click(screen.getByRole("button", { name: /清空/ }));
    expect(onChange).toHaveBeenCalledWith({ session_ids: [], days: [] });
  });

  it("unchecking a selected day removes it from `days`", async () => {
    const onChange = vi.fn();
    render(<ScopeSelector value={{ session_ids: [], days: ["2026-06-15"] }} onChange={onChange} />);
    await screen.findByText(/2026-06-15/);

    await userEvent.click(screen.getByRole("checkbox", { name: /2026-06-15/ }));
    expect(onChange).toHaveBeenCalledWith({ session_ids: [], days: [] });
  });
});
