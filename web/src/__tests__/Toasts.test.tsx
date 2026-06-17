import { act, render, renderHook, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Toasts, useToasts } from "../components/Toasts";

describe("Toasts", () => {
  it("uses an explicit close button", async () => {
    const onDismiss = vi.fn();
    render(<Toasts toasts={[{ id: 1, title: "失败", message: "API failed" }]} onDismiss={onDismiss} />);

    expect(screen.getByRole("alert")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /关闭/ }));
    expect(onDismiss).toHaveBeenCalledWith(1);
  });

  it("renders an action button for action toasts and runs the callback", async () => {
    const onAction = vi.fn();
    render(
      <Toasts
        toasts={[{ id: 1, title: "已接受 2 段", actionLabel: "撤销", onAction }]}
        onDismiss={vi.fn()}
      />
    );
    await userEvent.click(screen.getByRole("button", { name: "撤销" }));
    expect(onAction).toHaveBeenCalledTimes(1);
  });
});

describe("useToasts.pushAction", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("invoking the toast's onAction runs the callback then dismisses the toast", () => {
    const onAction = vi.fn();
    const { result } = renderHook(() => useToasts());

    act(() => {
      result.current.pushAction("已接受 2 段", "撤销", onAction);
    });
    expect(result.current.toasts).toHaveLength(1);
    expect(result.current.toasts[0].actionLabel).toBe("撤销");

    // The store wraps onAction so firing it (what the rendered button's onClick does) both
    // runs the user callback AND dismisses the toast.
    act(() => result.current.toasts[0].onAction!());

    expect(onAction).toHaveBeenCalledTimes(1);
    expect(result.current.toasts).toHaveLength(0); // dismissed after acting
  });

  it("auto-dismisses after the timer", () => {
    const onAction = vi.fn();
    const { result } = renderHook(() => useToasts());

    act(() => {
      result.current.pushAction("已接受 2 段", "撤销", onAction, 6000);
    });
    expect(result.current.toasts).toHaveLength(1);

    act(() => vi.advanceTimersByTime(5999));
    expect(result.current.toasts).toHaveLength(1); // not yet
    act(() => vi.advanceTimersByTime(1));
    expect(result.current.toasts).toHaveLength(0); // auto-dismissed
    expect(onAction).not.toHaveBeenCalled(); // auto-dismiss does NOT run the action
  });
});
