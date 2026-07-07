import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ResultEditor } from "../features/viewpoint/ResultEditor";
import type { ViewpointContent, ViewpointState } from "../api/types";
import { api } from "../api/client";

vi.mock("../api/client", () => ({
  api: {
    generateViewpoint: vi.fn().mockResolvedValue({ enqueued: true, session_id: "ses_1" }),
    editViewpoint: vi.fn().mockResolvedValue({}),
    clearViewpointEdit: vi.fn().mockResolvedValue({}),
    publishViewpoint: vi.fn().mockResolvedValue({ note_path: "Notes/ses_1.md", published_at: "2026-06-18T10:00:00+08:00" })
  }
}));

const content: ViewpointContent = {
  headline: "会话观点",
  summary: "讨论部署。",
  topics: ["部署"],
  decisions: [{ text: "本地部署", evidence_refs: ["seg_1"] }],
  todos: [{ text: "买机器", owner: "Paul", evidence_refs: ["seg_2"] }],
  open_questions: ["预算?"],
  core_conclusions: ["数据不出本机"],
  per_speaker: [
    { speaker_cluster_id: "spk_1", viewpoints: [{ text: "我支持", evidence_refs: ["seg_1"] }], sentiment: "positive", stance: "支持", latent_needs: ["安全"] }
  ]
};

function state(over: Partial<ViewpointState> = {}): ViewpointState {
  return {
    session_id: "ses_1",
    segments: [],
    prompt: { effective: "p", default: "p", is_override: false },
    generated: content,
    edited: null,
    effective: content,
    status: "draft",
    stale: false,
    has_generated: true,
    generating: false,
    published_at: null,
    note_path: null,
    ...over
  };
}

afterEach(() => vi.clearAllMocks());

describe("ResultEditor", () => {
  it("shows an empty state when nothing has been generated", () => {
    render(
      <ResultEditor
        vp={state({ generated: null, effective: null, has_generated: false })}
        onChanged={vi.fn()}
        onGenerate={vi.fn()}
      />
    );
    expect(screen.getByText(/尚未生成/)).toBeInTheDocument();
  });

  it("shows a generating spinner while generating", () => {
    render(<ResultEditor vp={state({ generating: true, has_generated: false })} onChanged={vi.fn()} onGenerate={vi.fn()} />);
    expect(screen.getByText(/生成中/)).toBeInTheDocument();
  });

  it("重新生成 generates directly when there is no edit", async () => {
    const onGenerate = vi.fn();
    render(<ResultEditor vp={state()} onChanged={vi.fn()} onGenerate={onGenerate} />);
    await userEvent.click(screen.getByRole("button", { name: /重新生成/ }));
    expect(onGenerate).toHaveBeenCalled();
  });

  it("重新生成 confirms first (via the Dialog prop) when an edit exists, only then generates", async () => {
    const onGenerate = vi.fn();
    const confirm = vi.fn(async () => false);
    const { rerender } = render(
      <ResultEditor vp={state({ edited: content, status: "edited" })} onChanged={vi.fn()} onGenerate={onGenerate} confirm={confirm} />
    );
    await userEvent.click(screen.getByRole("button", { name: /重新生成/ }));
    await waitFor(() => expect(confirm).toHaveBeenCalledWith(expect.objectContaining({ confirmLabel: "重新生成" })));
    expect(onGenerate).not.toHaveBeenCalled(); // user declined

    confirm.mockImplementation(async () => true);
    rerender(<ResultEditor vp={state({ edited: content, status: "edited" })} onChanged={vi.fn()} onGenerate={onGenerate} confirm={confirm} />);
    await userEvent.click(screen.getByRole("button", { name: /重新生成/ }));
    await waitFor(() => expect(onGenerate).toHaveBeenCalled());
  });

  it("editing the headline + blur calls editViewpoint with the full doc, evidence_refs preserved", async () => {
    const onChanged = vi.fn();
    render(<ResultEditor vp={state()} onChanged={onChanged} onGenerate={vi.fn()} />);

    const headline = screen.getByDisplayValue("会话观点");
    await userEvent.clear(headline);
    await userEvent.type(headline, "改过的标题");
    await userEvent.tab(); // blur

    await waitFor(() => expect(api.editViewpoint).toHaveBeenCalled());
    const [id, sent] = (api.editViewpoint as unknown as ReturnType<typeof vi.fn>).mock.calls[0] as [string, ViewpointContent];
    expect(id).toBe("ses_1");
    expect(sent.headline).toBe("改过的标题");
    // The full doc is assembled — evidence_refs + cluster ids are preserved verbatim.
    expect(sent.decisions[0].evidence_refs).toEqual(["seg_1"]);
    expect(sent.per_speaker[0].speaker_cluster_id).toBe("spk_1");
    expect(sent.per_speaker[0].viewpoints[0].evidence_refs).toEqual(["seg_1"]);
    expect(onChanged).toHaveBeenCalled();
  });

  it("surfaces a 400 validation message and keeps the user's edit", async () => {
    (api.editViewpoint as unknown as ReturnType<typeof vi.fn>).mockRejectedValueOnce(new Error("evidence_refs 越界"));
    render(<ResultEditor vp={state()} onChanged={vi.fn()} onGenerate={vi.fn()} />);

    const headline = screen.getByDisplayValue("会话观点");
    await userEvent.type(headline, "X");
    await userEvent.tab();

    expect(await screen.findByText(/evidence_refs 越界/)).toBeInTheDocument();
    // The edit is not lost.
    expect(screen.getByDisplayValue("会话观点X")).toBeInTheDocument();
  });

  it("撤销修改 reverts to the generated baseline via clearViewpointEdit", async () => {
    const onChanged = vi.fn();
    render(<ResultEditor vp={state({ edited: content, status: "edited" })} onChanged={onChanged} onGenerate={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: /撤销修改/ }));
    await waitFor(() => expect(api.clearViewpointEdit).toHaveBeenCalledWith("ses_1"));
    expect(onChanged).toHaveBeenCalled();
  });

  it("确认保存到 Obsidian publishes and shows the note path", async () => {
    render(<ResultEditor vp={state()} onChanged={vi.fn()} onGenerate={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: /确认保存到 Obsidian/ }));
    await waitFor(() => expect(api.publishViewpoint).toHaveBeenCalledWith("ses_1"));
    expect(await screen.findByText(/Notes\/ses_1\.md/)).toBeInTheDocument();
  });
});

describe("ResultEditor layout", () => {
  const css = readFileSync(resolve(process.cwd(), "src/styles.css"), "utf8");

  it("bounds the result card and scrolls the editable body instead of growing indefinitely", () => {
    expect(css).toMatch(/\.vp-result\s*\{[^}]*max-height:\s*min\(640px,\s*calc\(100vh - 160px\)\)[^}]*overflow:\s*hidden/);
    expect(css).toMatch(/\.vp-result-body\s*\{[^}]*overflow-y:\s*auto/);
  });
});
