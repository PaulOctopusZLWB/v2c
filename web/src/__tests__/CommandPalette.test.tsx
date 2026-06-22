import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { CommandPalette, type Command } from "../features/command/CommandPalette";

function makeCommands(run = vi.fn()): Command[] {
  return [
    { id: "go-review", title: "前往「审核」", group: "导航", run },
    { id: "go-speakers", title: "前往「声纹」", group: "导航", run },
    { id: "open-day", title: "打开 2087-05-10", group: "日期", keywords: "day", run }
  ];
}

describe("CommandPalette", () => {
  it("renders nothing when closed", () => {
    const { container } = render(<CommandPalette open={false} commands={makeCommands()} onClose={vi.fn()} />);
    expect(container.querySelector("[role='dialog']")).toBeNull();
  });

  it("renders all commands across both groups when open", () => {
    render(<CommandPalette open commands={makeCommands()} onClose={vi.fn()} />);
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByText("前往「审核」")).toBeInTheDocument();
    expect(screen.getByText("前往「声纹」")).toBeInTheDocument();
    expect(screen.getByText("打开 2087-05-10")).toBeInTheDocument();
    // Group headers render.
    expect(screen.getByText("导航")).toBeInTheDocument();
    expect(screen.getByText("日期")).toBeInTheDocument();
  });

  it("filters the list as you type (case-insensitive substring/fuzzy)", async () => {
    render(<CommandPalette open commands={makeCommands()} onClose={vi.fn()} />);
    const input = screen.getByRole("textbox");
    await userEvent.type(input, "声纹");
    expect(screen.getByText("前往「声纹」")).toBeInTheDocument();
    expect(screen.queryByText("前往「审核」")).toBeNull();
    expect(screen.queryByText("打开 2087-05-10")).toBeNull();
  });

  it("ArrowDown then Enter runs the highlighted command and closes", async () => {
    const reviewRun = vi.fn();
    const speakersRun = vi.fn();
    const commands: Command[] = [
      { id: "go-review", title: "前往「审核」", group: "导航", run: reviewRun },
      { id: "go-speakers", title: "前往「声纹」", group: "导航", run: speakersRun }
    ];
    const onClose = vi.fn();
    render(<CommandPalette open commands={commands} onClose={onClose} />);
    const input = screen.getByRole("textbox");
    // First item is highlighted by default; ArrowDown moves to the second.
    await userEvent.type(input, "{ArrowDown}{Enter}");
    expect(speakersRun).toHaveBeenCalledTimes(1);
    expect(reviewRun).not.toHaveBeenCalled();
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("Esc calls onClose", async () => {
    const onClose = vi.fn();
    render(<CommandPalette open commands={makeCommands()} onClose={onClose} />);
    await userEvent.type(screen.getByRole("textbox"), "{Escape}");
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("clicking the overlay closes", async () => {
    const onClose = vi.fn();
    render(<CommandPalette open commands={makeCommands()} onClose={onClose} />);
    // Portalled to #overlay-root (outside the render container), so query the document.
    const overlay = document.querySelector(".cmdk-overlay") as HTMLElement;
    await userEvent.click(overlay);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("clicking a command runs it and closes", async () => {
    const run = vi.fn();
    const onClose = vi.fn();
    render(
      <CommandPalette
        open
        commands={[{ id: "go-review", title: "前往「审核」", group: "导航", run }]}
        onClose={onClose}
      />
    );
    await userEvent.click(screen.getByText("前往「审核」"));
    expect(run).toHaveBeenCalledTimes(1);
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
