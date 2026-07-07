import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { Sidebar } from "../features/workspace/Sidebar";

const baseProps = {
  onSelect: vi.fn(),
  onOpenPalette: vi.fn(),
  pipelineRunning: false,
  days: [] as Array<{ day: string; session_count: number }>,
  onOpenDay: vi.fn()
};

describe("Sidebar", () => {
  it("renders the five main nav items with digit hints, plus 设置 pinned at the bottom", () => {
    render(<Sidebar {...baseProps} active="home" />);
    const tabs = screen.getAllByRole("tab");
    // 可访问名 = 纯标签(字形/快捷键是装饰);顺序:5 主项 + 设置钉底。
    expect(tabs.map((t) => t.getAttribute("aria-label"))).toEqual(["今日", "管道", "审核", "声纹", "记忆", "总结", "设置"]);
    // 数字快捷键角标 1–5 依次渲染。
    expect(tabs.slice(0, 6).map((t) => t.querySelector(".sidebar-item-key")?.textContent)).toEqual(["1", "2", "3", "4", "5", "6"]);
  });

  it("marks the active item with aria-current and calls onSelect on click", async () => {
    const onSelect = vi.fn();
    render(<Sidebar {...baseProps} active="review" onSelect={onSelect} />);
    expect(screen.getByRole("tab", { name: /审核/ })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("tab", { name: /声纹/ })).not.toHaveAttribute("aria-current");
    await userEvent.click(screen.getByRole("tab", { name: /声纹/ }));
    expect(onSelect).toHaveBeenCalledWith("speakers");
  });

  it("shows the review pending badge and the pipeline breathing dot only when applicable", () => {
    const { rerender } = render(<Sidebar {...baseProps} active="home" reviewPending={12} pipelineRunning />);
    expect(screen.getByText("12")).toHaveClass("sidebar-badge");
    expect(screen.getByLabelText("运行中")).toBeInTheDocument();

    rerender(<Sidebar {...baseProps} active="home" reviewPending={0} pipelineRunning={false} />);
    expect(screen.queryByText("0")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("运行中")).not.toBeInTheDocument();
  });

  it("opens the command palette from the ⌘K search pill", async () => {
    const onOpenPalette = vi.fn();
    render(<Sidebar {...baseProps} active="home" onOpenPalette={onOpenPalette} />);
    await userEvent.click(screen.getByRole("button", { name: /搜索或跳转/ }));
    expect(onOpenPalette).toHaveBeenCalled();
  });

  it("lists recent days under 资料库 and opens a day on click", async () => {
    const onOpenDay = vi.fn();
    render(
      <Sidebar
        {...baseProps}
        active="home"
        onOpenDay={onOpenDay}
        days={[
          { day: "2087-05-11", session_count: 3 },
          { day: "2087-05-10", session_count: 5 }
        ]}
      />
    );
    expect(screen.getByText("资料库")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /2087-05-10/ }));
    expect(onOpenDay).toHaveBeenCalledWith("2087-05-10");
  });

  it("shows the local node status row", () => {
    render(<Sidebar {...baseProps} active="home" />);
    expect(screen.getByText(/本地节点 ·/)).toBeInTheDocument();
  });
});
