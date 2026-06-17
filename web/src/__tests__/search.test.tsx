import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { CommandPalette, type Command } from "../features/command/CommandPalette";

/**
 * Search mode wiring on the command palette: the parent owns the async results (it debounces
 * api.search) and injects them as `extraItems`, while `onQueryChange` lets it observe the query.
 * The palette renders extraItems in their own group, unfiltered (they're server-filtered already).
 */
describe("CommandPalette search mode", () => {
  it("reports the query to onQueryChange as the user types", async () => {
    const onQueryChange = vi.fn();
    render(<CommandPalette open commands={[]} onClose={vi.fn()} onQueryChange={onQueryChange} />);
    await userEvent.type(screen.getByRole("textbox"), "数据");
    expect(onQueryChange).toHaveBeenLastCalledWith("数据");
  });

  it("renders injected extraItems and runs the right one on click", async () => {
    const jump = vi.fn();
    const onClose = vi.fn();
    const extraItems: Command[] = [
      { id: "seg-1", title: "数据不出本机", hint: "2087-05-10 · self", group: "转写搜索", run: jump }
    ];
    render(
      <CommandPalette open commands={[]} onClose={onClose} extraItems={extraItems} onQueryChange={vi.fn()} />
    );
    expect(screen.getByText("转写搜索")).toBeInTheDocument();
    await userEvent.click(screen.getByText("数据不出本机"));
    expect(jump).toHaveBeenCalledTimes(1);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("shows extraItems even when they don't fuzzy-match the typed query (server already filtered)", async () => {
    const extraItems: Command[] = [
      { id: "seg-1", title: "命中率达标", group: "转写搜索", run: vi.fn() }
    ];
    render(
      <CommandPalette open commands={[]} onClose={vi.fn()} extraItems={extraItems} onQueryChange={vi.fn()} />
    );
    // Typing a query that the title does NOT contain must not hide the server-provided hit.
    await userEvent.type(screen.getByRole("textbox"), "数据");
    expect(screen.getByText("命中率达标")).toBeInTheDocument();
  });
});
