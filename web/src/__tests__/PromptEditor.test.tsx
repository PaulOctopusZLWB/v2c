import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PromptEditor } from "../features/viewpoint/PromptEditor";
import type { ViewpointPrompt } from "../api/types";
import { api } from "../api/client";

vi.mock("../api/client", () => ({
  api: {
    setSessionPromptOverride: vi.fn().mockResolvedValue({ effective: "x", default: "y", is_override: true }),
    setSessionPrompt: vi.fn().mockResolvedValue({ template: "x", default: "y" })
  }
}));

const prompt: ViewpointPrompt = { effective: "请总结本会话。", default: "默认模板", is_override: false };

afterEach(() => vi.clearAllMocks());

describe("PromptEditor", () => {
  it("seeds the textarea from the effective prompt", async () => {
    render(<PromptEditor sessionId="ses_1" prompt={prompt} onChanged={vi.fn()} />);
    await openDetails();
    expect(screen.getByRole("textbox")).toHaveValue("请总结本会话。");
  });

  it("保存(本会话) calls setSessionPromptOverride with the edited text", async () => {
    const onChanged = vi.fn();
    render(<PromptEditor sessionId="ses_1" prompt={prompt} onChanged={onChanged} />);
    await openDetails();
    const box = screen.getByRole("textbox");
    await userEvent.clear(box);
    await userEvent.type(box, "新的提示词");
    await userEvent.click(screen.getByRole("button", { name: /保存\(本会话\)/ }));
    await waitFor(() => expect(api.setSessionPromptOverride).toHaveBeenCalledWith("ses_1", "新的提示词"));
    expect(onChanged).toHaveBeenCalled();
  });

  it("重置 calls setSessionPromptOverride with null", async () => {
    render(<PromptEditor sessionId="ses_1" prompt={{ ...prompt, is_override: true }} onChanged={vi.fn()} />);
    await openDetails();
    await userEvent.click(screen.getByRole("button", { name: /重置/ }));
    await waitFor(() => expect(api.setSessionPromptOverride).toHaveBeenCalledWith("ses_1", null));
  });

  it("shows 本会话自定义 when the prompt is an override", async () => {
    render(<PromptEditor sessionId="ses_1" prompt={{ ...prompt, is_override: true }} onChanged={vi.fn()} />);
    await openDetails();
    expect(screen.getByText(/本会话自定义/)).toBeInTheDocument();
  });
});

/** Open the collapsible prompt section so its controls render. */
async function openDetails() {
  const summary = screen.getByText(/会话提示词/);
  await userEvent.click(summary);
}
