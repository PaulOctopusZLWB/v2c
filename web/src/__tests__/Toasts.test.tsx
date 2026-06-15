import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { Toasts } from "../components/Toasts";

describe("Toasts", () => {
  it("uses an explicit close button", async () => {
    const onDismiss = vi.fn();
    render(<Toasts toasts={[{ id: 1, title: "失败", message: "API failed" }]} onDismiss={onDismiss} />);

    expect(screen.getByRole("alert")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /关闭/ }));
    expect(onDismiss).toHaveBeenCalledWith(1);
  });
});
