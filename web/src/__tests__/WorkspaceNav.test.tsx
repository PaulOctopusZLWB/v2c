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

  it("renders a session's name when set, else the time label", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      if (url === "/api/transcripts/days/2087-05-10/sessions")
        return new Response(JSON.stringify({ day: "2087-05-10", sessions: [
          { session_id: "ses_named", started_at: "2087-05-10T08:00:00+08:00", segment_count: 3, review_status: "pending_review", name: "团队晨会" }
        ] }), { status: 200 });
      return new Response("{}", { status: 200 });
    }));
    const days = [{ day: "2087-05-10", session_count: 1 }];
    render(<WorkspaceNav days={days} selectedDay="2087-05-10" onSelectDay={vi.fn()} onSelectSession={vi.fn()} />);
    await waitFor(() => expect(screen.getByText(/团队晨会/)).toBeInTheDocument());
  });

  it("rename: clicking ✎ calls onRenameSession with the new name, then refreshes", async () => {
    const onRenameSession = vi.fn(async () => undefined);
    const days = [{ day: "2087-05-10", session_count: 1 }];
    vi.spyOn(window, "prompt").mockReturnValue("新名字");
    render(
      <WorkspaceNav
        days={days}
        selectedDay="2087-05-10"
        onSelectDay={vi.fn()}
        onSelectSession={vi.fn()}
        onRenameSession={onRenameSession}
        onDeleteSession={vi.fn()}
      />
    );
    await waitFor(() => expect(screen.getByRole("button", { name: /ses_1/ })).toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: /重命名/ }));
    expect(onRenameSession).toHaveBeenCalledWith("ses_1", "新名字");
  });

  it("delete: clicking 🗑 (confirm true) calls onDeleteSession with the id", async () => {
    const onDeleteSession = vi.fn(async () => undefined);
    const days = [{ day: "2087-05-10", session_count: 1 }];
    vi.spyOn(window, "confirm").mockReturnValue(true);
    render(
      <WorkspaceNav
        days={days}
        selectedDay="2087-05-10"
        onSelectDay={vi.fn()}
        onSelectSession={vi.fn()}
        onRenameSession={vi.fn()}
        onDeleteSession={onDeleteSession}
      />
    );
    await waitFor(() => expect(screen.getByRole("button", { name: /ses_1/ })).toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: /删除/ }));
    expect(onDeleteSession).toHaveBeenCalledWith("ses_1");
  });

  it("delete: confirm false does NOT call onDeleteSession", async () => {
    const onDeleteSession = vi.fn(async () => undefined);
    const days = [{ day: "2087-05-10", session_count: 1 }];
    vi.spyOn(window, "confirm").mockReturnValue(false);
    render(
      <WorkspaceNav
        days={days}
        selectedDay="2087-05-10"
        onSelectDay={vi.fn()}
        onSelectSession={vi.fn()}
        onRenameSession={vi.fn()}
        onDeleteSession={onDeleteSession}
      />
    );
    await waitFor(() => expect(screen.getByRole("button", { name: /ses_1/ })).toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: /删除/ }));
    expect(onDeleteSession).not.toHaveBeenCalled();
  });

  it("renders a per-day 处理中 / 可审 badge from the dayStatus prop", () => {
    const days = [
      { day: "2087-05-10", session_count: 2 },
      { day: "2087-05-11", session_count: 0 }
    ];
    const dayStatus = [
      { day: "2087-05-10", session_count: 2, active_count: 0, total_count: 5, status: "ready" as const },
      { day: "2087-05-11", session_count: 0, active_count: 3, total_count: 5, status: "processing" as const }
    ];
    render(
      <WorkspaceNav
        days={days}
        dayStatus={dayStatus}
        selectedDay={null}
        onSelectDay={vi.fn()}
        onSelectSession={vi.fn()}
      />
    );
    const ready = screen.getByRole("button", { name: /2087-05-10/ });
    expect(ready).toHaveTextContent("可审");
    const processing = screen.getByRole("button", { name: /2087-05-11/ });
    expect(processing).toHaveTextContent("处理中");
  });
});
