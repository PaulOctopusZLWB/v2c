import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import {
  Button,
  EmptyState,
  InspectorPanel,
  SegmentedControl,
  Skeleton,
  StatusBadge,
  WorkflowStepper
} from "../components/ui";
import { Select } from "../components/ui/Select";

describe("UI primitives", () => {
  it("renders Button variants with icon and busy state", () => {
    render(
      <Button variant="primary" icon="run" busy>
        启动
      </Button>
    );
    const button = screen.getByRole("button", { name: "启动" });
    expect(button).toHaveClass("primary");
    expect(button).toHaveAttribute("aria-busy", "true");
    expect(button.querySelector(".spinner")).toBeInTheDocument();
  });

  it("renders SegmentedControl and calls onChange", async () => {
    const onChange = vi.fn();
    render(
      <SegmentedControl
        ariaLabel="视图"
        value="map"
        onChange={onChange}
        options={[
          { value: "map", label: "地图" },
          { value: "list", label: "列表" }
        ]}
      />
    );
    expect(screen.getByRole("tab", { name: "地图" })).toHaveAttribute("aria-selected", "true");
    await userEvent.click(screen.getByRole("tab", { name: "列表" }));
    expect(onChange).toHaveBeenCalledWith("list");
  });

  it("renders semantic StatusBadge", () => {
    render(<StatusBadge status="warning">存疑</StatusBadge>);
    expect(screen.getByText("存疑")).toHaveClass("badge", "s-needs_fix");
  });

  it("renders EmptyState with recovery action", async () => {
    const onAction = vi.fn();
    render(
      <EmptyState icon="inbox" title="没有数据" description="先选择范围。" actionLabel="刷新" onAction={onAction} />
    );
    expect(screen.getByRole("heading", { name: "没有数据" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "刷新" }));
    expect(onAction).toHaveBeenCalledTimes(1);
  });

  it("renders Skeleton as status", () => {
    render(<Skeleton label="正在载入人物" rows={3} />);
    expect(screen.getByRole("status", { name: "正在载入人物" })).toBeInTheDocument();
    expect(document.querySelectorAll(".skeleton-row")).toHaveLength(3);
  });

  it("renders InspectorPanel sections", () => {
    render(
      <InspectorPanel title="人物" subtitle="证据面板">
        <p>吴博</p>
      </InspectorPanel>
    );
    expect(screen.getByRole("complementary", { name: "人物" })).toBeInTheDocument();
    expect(screen.getByText("证据面板")).toBeInTheDocument();
  });

  it("renders WorkflowStepper step states", () => {
    render(
      <WorkflowStepper
        ariaLabel="声纹流程"
        steps={[
          { id: "scope", label: "选择范围", state: "complete" },
          { id: "project", label: "投射", state: "current" },
          { id: "verify", label: "验证", state: "blocked" }
        ]}
      />
    );
    expect(screen.getByRole("list", { name: "声纹流程" })).toBeInTheDocument();
    expect(screen.getByText("选择范围")).toHaveClass("workflow-step-label");
    expect(screen.getByText("验证").closest(".workflow-step")).toHaveAttribute("data-state", "blocked");
  });

  it("keeps a portalled Select open when its own menu scrolls", async () => {
    const options = Array.from({ length: 24 }, (_, i) => ({
      value: `person-${i}`,
      label: `人物 ${i}`
    }));
    render(
      <Select
        ariaLabel="标注为"
        value=""
        onChange={vi.fn()}
        options={options}
        placeholder="选择人物…"
      />
    );

    await userEvent.click(screen.getByRole("combobox", { name: "标注为" }));
    const listbox = await screen.findByRole("listbox", { name: "标注为" });
    fireEvent.scroll(listbox);

    expect(screen.getByRole("listbox", { name: "标注为" })).toBeInTheDocument();
  });

  it("still closes a portalled Select on unrelated window scroll", async () => {
    render(
      <Select
        ariaLabel="标注为"
        value=""
        onChange={vi.fn()}
        options={[{ value: "per_a", label: "韩文巧" }]}
        placeholder="选择人物…"
      />
    );

    await userEvent.click(screen.getByRole("combobox", { name: "标注为" }));
    expect(await screen.findByRole("listbox", { name: "标注为" })).toBeInTheDocument();
    fireEvent.scroll(window);

    await waitFor(() => expect(screen.queryByRole("listbox", { name: "标注为" })).not.toBeInTheDocument());
  });
});
