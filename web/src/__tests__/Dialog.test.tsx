import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { DialogHost, useDialog, type ConfirmOptions, type PromptOptions } from "../components/ui/Dialog";

/** Harness:按钮触发 confirm/prompt,把 promise 结果写到屏幕上以便断言。 */
function ConfirmHarness({ opts }: { opts: ConfirmOptions }) {
  const { request, confirm, settle } = useDialog();
  return (
    <>
      <button
        type="button"
        onClick={() => void confirm(opts).then((ok) => document.getElementById("out")!.append(ok ? "OK" : "CANCEL"))}
      >
        触发确认
      </button>
      <div id="out" data-testid="out" />
      <DialogHost request={request} onSettle={settle} />
    </>
  );
}

function PromptHarness({ opts }: { opts: PromptOptions }) {
  const { request, promptText, settle } = useDialog();
  return (
    <>
      <button
        type="button"
        onClick={() => void promptText(opts).then((v) => document.getElementById("out")!.append(v === null ? "NULL" : `V:${v}`))}
      >
        触发输入
      </button>
      <div id="out" data-testid="out" />
      <DialogHost request={request} onSettle={settle} />
    </>
  );
}

describe("Dialog — 危险确认", () => {
  const opts: ConfirmOptions = { title: "删除会话「周会」?", body: <>此操作<strong>不可撤销</strong>。</>, confirmLabel: "删除" };

  it("renders title/body, resolves true on the danger button, and closes", async () => {
    render(<ConfirmHarness opts={opts} />);
    await userEvent.click(screen.getByRole("button", { name: "触发确认" }));
    expect(screen.getByRole("alertdialog", { name: "删除会话「周会」?" })).toBeInTheDocument();
    expect(screen.getByText("不可撤销")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /删除/ }));
    await waitFor(() => expect(screen.getByTestId("out")).toHaveTextContent("OK"));
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  });

  it("Esc resolves false; plain Enter does NOT confirm; ⌘Enter confirms", async () => {
    const { unmount } = render(<ConfirmHarness opts={opts} />);
    await userEvent.click(screen.getByRole("button", { name: "触发确认" }));
    // 普通 Enter 被吞掉:对话框保持打开,未结算。
    await userEvent.keyboard("{Enter}");
    expect(screen.getByRole("alertdialog")).toBeInTheDocument();
    expect(screen.getByTestId("out")).toHaveTextContent("");
    // Esc = 取消。
    await userEvent.keyboard("{Escape}");
    await waitFor(() => expect(screen.getByTestId("out")).toHaveTextContent("CANCEL"));
    unmount();

    render(<ConfirmHarness opts={opts} />);
    await userEvent.click(screen.getByRole("button", { name: "触发确认" }));
    await userEvent.keyboard("{Meta>}{Enter}{/Meta}");
    await waitFor(() => expect(screen.getByTestId("out")).toHaveTextContent("OK"));
  });

  it("clicking the backdrop cancels", async () => {
    render(<ConfirmHarness opts={opts} />);
    await userEvent.click(screen.getByRole("button", { name: "触发确认" }));
    await userEvent.click(document.querySelector(".dialog-overlay") as HTMLElement);
    await waitFor(() => expect(screen.getByTestId("out")).toHaveTextContent("CANCEL"));
  });

  it("focuses the safe 取消 button by default", async () => {
    render(<ConfirmHarness opts={opts} />);
    await userEvent.click(screen.getByRole("button", { name: "触发确认" }));
    expect(screen.getByRole("button", { name: /取消/ })).toHaveFocus();
  });
});

describe("Dialog — 重命名/输入", () => {
  it("pre-fills initial value, Enter saves the typed value", async () => {
    render(<PromptHarness opts={{ title: "重命名会话", initial: "旧名" }} />);
    await userEvent.click(screen.getByRole("button", { name: "触发输入" }));
    const input = screen.getByRole("textbox", { name: "重命名会话" });
    expect(input).toHaveValue("旧名");
    expect(input).toHaveFocus();
    await userEvent.clear(input);
    await userEvent.type(input, "周会 · 项目排期");
    await userEvent.keyboard("{Enter}");
    await waitFor(() => expect(screen.getByTestId("out")).toHaveTextContent("V:周会 · 项目排期"));
  });

  it("Esc resolves null (cancelled)", async () => {
    render(<PromptHarness opts={{ title: "重命名会话", initial: "旧名" }} />);
    await userEvent.click(screen.getByRole("button", { name: "触发输入" }));
    await userEvent.keyboard("{Escape}");
    await waitFor(() => expect(screen.getByTestId("out")).toHaveTextContent("NULL"));
  });

  it("AI 建议:click 采用 fills the input (does not save); Tab adopts too", async () => {
    render(<PromptHarness opts={{ title: "重命名会话", initial: "", suggestion: "排期与联调计划对齐会" }} />);
    await userEvent.click(screen.getByRole("button", { name: "触发输入" }));
    expect(screen.getByText(/AI 建议/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /采用/ }));
    expect(screen.getByRole("textbox", { name: "重命名会话" })).toHaveValue("排期与联调计划对齐会");
    // 仍未保存:对话框还开着。
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    // 清空后 Tab 重新采用建议。
    await userEvent.clear(screen.getByRole("textbox", { name: "重命名会话" }));
    await userEvent.keyboard("{Tab}");
    expect(screen.getByRole("textbox", { name: "重命名会话" })).toHaveValue("排期与联调计划对齐会");
    await userEvent.keyboard("{Enter}");
    await waitFor(() => expect(screen.getByTestId("out")).toHaveTextContent("V:排期与联调计划对齐会"));
  });

  it("保存 button resolves the current value", async () => {
    render(<PromptHarness opts={{ title: "重命名会话", initial: "会议" }} />);
    await userEvent.click(screen.getByRole("button", { name: "触发输入" }));
    await userEvent.click(screen.getByRole("button", { name: /保存/ }));
    await waitFor(() => expect(screen.getByTestId("out")).toHaveTextContent("V:会议"));
  });
});
