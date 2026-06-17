import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ReviewQueue } from "../features/transcript/ReviewQueue";
import { api } from "../api/client";
import type { ReviewQueueItem } from "../api/types";

const items: ReviewQueueItem[] = [
  { session_id: "ses_a", day: "2087-05-10", started_at: "2087-05-10T09:00:00+08:00", pending: 4, total: 10, speakers: 2, has_flag: 1 },
  { session_id: "ses_b", day: "2087-05-11", started_at: "2087-05-11T14:30:00+08:00", pending: 2, total: 6, speakers: 1, has_flag: 0 }
];

describe("ReviewQueue", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders each session with its pending count and flags the needs_fix one", async () => {
    vi.spyOn(api, "reviewQueue").mockResolvedValue({ queue: items });
    render(<ReviewQueue activeSessionId={null} onOpen={vi.fn()} />);

    // Both items render with their pending counts ("N 待审").
    expect(await screen.findByText(/4 待审/)).toBeInTheDocument();
    expect(screen.getByText(/2 待审/)).toBeInTheDocument();

    // The flagged session (ses_a) shows the ⚑ marker; the other does not.
    const flagged = screen.getByRole("button", { name: /09:00/ });
    expect(flagged).toHaveTextContent("⚑");
    const unflagged = screen.getByRole("button", { name: /14:30/ });
    expect(unflagged).not.toHaveTextContent("⚑");
  });

  it("calls onOpen with the session_id + day when an item is clicked", async () => {
    vi.spyOn(api, "reviewQueue").mockResolvedValue({ queue: items });
    const onOpen = vi.fn();
    render(<ReviewQueue activeSessionId={null} onOpen={onOpen} />);

    await userEvent.click(await screen.findByRole("button", { name: /09:00/ }));
    expect(onOpen).toHaveBeenCalledWith("ses_a", "2087-05-10");
  });

  it("highlights the active session", async () => {
    vi.spyOn(api, "reviewQueue").mockResolvedValue({ queue: items });
    render(<ReviewQueue activeSessionId="ses_b" onOpen={vi.fn()} />);

    const active = await screen.findByRole("button", { name: /14:30/ });
    expect(active.className).toContain("active");
  });

  it("shows the done state when the queue is empty", async () => {
    vi.spyOn(api, "reviewQueue").mockResolvedValue({ queue: [] });
    render(<ReviewQueue activeSessionId={null} onOpen={vi.fn()} />);

    expect(await screen.findByText(/全部已审完/)).toBeInTheDocument();
  });

  it("refetches when the version prop bumps", async () => {
    const spy = vi.spyOn(api, "reviewQueue").mockResolvedValue({ queue: items });
    const { rerender } = render(<ReviewQueue activeSessionId={null} onOpen={vi.fn()} version={0} />);
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));

    rerender(<ReviewQueue activeSessionId={null} onOpen={vi.fn()} version={1} />);
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(2));
  });
});
