import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { Tabs } from "../features/workspace/Tabs";

describe("Tabs", () => {
  it("renders the six tab buttons with Chinese labels, 首页 first", () => {
    render(<Tabs active="review" onSelect={vi.fn()} />);
    const tabs = screen.getAllByRole("tab");
    expect(tabs).toHaveLength(6);
    expect(tabs.map((t) => t.textContent)).toEqual(["首页", "录入", "身份", "转写审核", "总结", "设置"]);
  });

  it("marks the active tab with aria-current", () => {
    render(<Tabs active="review" onSelect={vi.fn()} />);
    expect(screen.getByRole("tab", { name: "转写审核" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("tab", { name: "身份" })).not.toHaveAttribute("aria-current");
  });

  it("calls onSelect with the tab id when clicked", async () => {
    const onSelect = vi.fn();
    render(<Tabs active="review" onSelect={onSelect} />);
    await userEvent.click(screen.getByRole("tab", { name: "身份" }));
    expect(onSelect).toHaveBeenCalledWith("speakers");
  });
});
