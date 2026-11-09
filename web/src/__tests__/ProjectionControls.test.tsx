import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ProjectionControls, PROJ_DEFAULTS } from "../features/viz/ProjectionControls";

describe("ProjectionControls", () => {
  it("only exposes UMAP and preserves tunable parameters", async () => {
    const onChange = vi.fn();
    const onApply = vi.fn();
    render(<ProjectionControls value={PROJ_DEFAULTS} onChange={onChange} onApply={onApply}
      scopeStatus={{ total: 100, embedded: 99, pending: 1, limit: 16000 }} />);
    expect(screen.queryByRole("button", { name: /PCA|t-SNE/ })).not.toBeInTheDocument();
    expect(screen.getByText(/另有 1 段未提取/)).toBeInTheDocument();
    await userEvent.click(screen.getByText("UMAP 参数"));
    fireEvent.change(screen.getByLabelText("n_neighbors"), { target: { value: "30" } });
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ n_neighbors: 30 }));
    expect(onApply).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: /生成云图/ }));
    expect(onApply).toHaveBeenCalledOnce();
  });

  it("permits 16,000 points and blocks 16,001 without sampling", () => {
    const props = { value: PROJ_DEFAULTS, onChange: vi.fn(), onApply: vi.fn() };
    const { rerender } = render(<ProjectionControls {...props} scopeStatus={{ total: 16000, embedded: 16000, pending: 0, limit: 16000 }} />);
    expect(screen.getByRole("button", { name: /生成云图/ })).toBeEnabled();
    rerender(<ProjectionControls {...props} processing scopeStatus={{ total: 16000, embedded: 16000, pending: 0, limit: 16000 }} />);
    expect(screen.getByRole("button", { name: /正在生成/ })).toBeDisabled();
    rerender(<ProjectionControls {...props} scopeStatus={{ total: 16001, embedded: 16001, pending: 0, limit: 16000 }} />);
    expect(screen.getByRole("button", { name: /生成云图/ })).toBeDisabled();
    expect(screen.getByText(/超过上限 1 段/)).toBeInTheDocument();
    expect(screen.queryByText(/采样/)).not.toBeInTheDocument();
  });

  it("blocks empty, too-small, pending and failed counts", () => {
    const props = { value: PROJ_DEFAULTS, onChange: vi.fn(), onApply: vi.fn() };
    const { rerender } = render(<ProjectionControls {...props} scopeStatus={{ total: 4, embedded: 4, pending: 0, limit: 16000 }} />);
    expect(screen.getByRole("button", { name: /生成云图/ })).toBeDisabled();
    expect(screen.getByText(/至少需要 5 段/)).toBeInTheDocument();
    rerender(<ProjectionControls {...props} counting />);
    expect(screen.getByRole("button", { name: /生成云图/ })).toBeDisabled();
    rerender(<ProjectionControls {...props} scopeError="无法读取" />);
    expect(screen.getByRole("button", { name: /生成云图/ })).toBeDisabled();
  });
});
