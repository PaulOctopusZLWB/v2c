import { renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { eventToCombo, useHotkeys } from "../features/command/useHotkeys";

/** Dispatch a keydown on window with an optional target element. */
function press(init: KeyboardEventInit & { target?: EventTarget }) {
  const { target, ...rest } = init;
  const event = new KeyboardEvent("keydown", { bubbles: true, cancelable: true, ...rest });
  if (target) Object.defineProperty(event, "target", { value: target, enumerable: true });
  window.dispatchEvent(event);
  return event;
}

describe("eventToCombo", () => {
  it("maps metaKey+k to mod+k", () => {
    expect(eventToCombo(new KeyboardEvent("keydown", { key: "k", metaKey: true }))).toBe("mod+k");
  });

  it("maps ctrlKey+k to mod+k", () => {
    expect(eventToCombo(new KeyboardEvent("keydown", { key: "k", ctrlKey: true }))).toBe("mod+k");
  });

  it("maps Escape to escape and lowercases plain letters", () => {
    expect(eventToCombo(new KeyboardEvent("keydown", { key: "Escape" }))).toBe("escape");
    expect(eventToCombo(new KeyboardEvent("keydown", { key: "J" }))).toBe("j");
  });

  it("includes the shift modifier", () => {
    expect(eventToCombo(new KeyboardEvent("keydown", { key: "/", shiftKey: true }))).toBe("shift+/");
  });
});

describe("useHotkeys", () => {
  afterEach(() => vi.restoreAllMocks());

  it("fires the handler for mod+k (metaKey)", () => {
    const onModK = vi.fn();
    renderHook(() => useHotkeys({ "mod+k": onModK }));
    press({ key: "k", metaKey: true });
    expect(onModK).toHaveBeenCalledTimes(1);
  });

  it("fires escape even when the target is an input", () => {
    const onEsc = vi.fn();
    renderHook(() => useHotkeys({ escape: onEsc }));
    const input = document.createElement("input");
    press({ key: "Escape", target: input });
    expect(onEsc).toHaveBeenCalledTimes(1);
  });

  it("does NOT fire a plain letter when the target is an input", () => {
    const onJ = vi.fn();
    renderHook(() => useHotkeys({ j: onJ }));
    const input = document.createElement("input");
    press({ key: "j", target: input });
    expect(onJ).not.toHaveBeenCalled();
  });

  it("fires a plain letter when the target is not editable", () => {
    const onJ = vi.fn();
    renderHook(() => useHotkeys({ j: onJ }));
    press({ key: "j", target: document.createElement("div") });
    expect(onJ).toHaveBeenCalledTimes(1);
  });

  it("does NOT fire a single key when the target is a <button> (defer to its activation)", () => {
    const onSpace = vi.fn();
    const onA = vi.fn();
    renderHook(() => useHotkeys({ space: onSpace, a: onA }));
    const button = document.createElement("button");
    press({ key: " ", target: button });
    press({ key: "a", target: button });
    expect(onSpace).not.toHaveBeenCalled();
    expect(onA).not.toHaveBeenCalled();
  });

  it("does NOT fire a single key on a link with href or an activatable ARIA role", () => {
    const onSpace = vi.fn();
    renderHook(() => useHotkeys({ space: onSpace }));

    const link = document.createElement("a");
    link.setAttribute("href", "#");
    press({ key: " ", target: link });
    expect(onSpace).not.toHaveBeenCalled();

    const roleButton = document.createElement("div");
    roleButton.setAttribute("role", "button");
    press({ key: " ", target: roleButton });
    expect(onSpace).not.toHaveBeenCalled();

    const tab = document.createElement("div");
    tab.setAttribute("role", "tab");
    press({ key: " ", target: tab });
    expect(onSpace).not.toHaveBeenCalled();
  });

  it("still fires a single key on a plain link with NO href (not activatable)", () => {
    const onSpace = vi.fn();
    renderHook(() => useHotkeys({ space: onSpace }));
    const anchor = document.createElement("a"); // no href → not a control
    press({ key: " ", target: anchor });
    expect(onSpace).toHaveBeenCalledTimes(1);
  });

  it("escape still fires even when the target is a <button> (ALWAYS_ACTIVE bypasses)", () => {
    const onEsc = vi.fn();
    renderHook(() => useHotkeys({ escape: onEsc }));
    press({ key: "Escape", target: document.createElement("button") });
    expect(onEsc).toHaveBeenCalledTimes(1);
  });

  it("removes the listener on unmount", () => {
    const onModK = vi.fn();
    const { unmount } = renderHook(() => useHotkeys({ "mod+k": onModK }));
    unmount();
    press({ key: "k", metaKey: true });
    expect(onModK).not.toHaveBeenCalled();
  });

  it("does nothing when disabled", () => {
    const onModK = vi.fn();
    renderHook(() => useHotkeys({ "mod+k": onModK }, { enabled: false }));
    press({ key: "k", metaKey: true });
    expect(onModK).not.toHaveBeenCalled();
  });

  it("re-binds when the bindings object changes", () => {
    const first = vi.fn();
    const second = vi.fn();
    const { rerender } = renderHook(({ b }) => useHotkeys(b), {
      initialProps: { b: { "mod+k": first } as Record<string, (e: KeyboardEvent) => void> }
    });
    rerender({ b: { "mod+k": second } });
    press({ key: "k", metaKey: true });
    expect(first).not.toHaveBeenCalled();
    expect(second).toHaveBeenCalledTimes(1);
  });
});
