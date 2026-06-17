import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ProjectionControls } from "../features/viz/ProjectionControls";
import type { ProjectionRequest } from "../api/types";

/** The tunable param subset ProjectionControls owns (method + per-method params). */
type Params = Pick<
  ProjectionRequest,
  "method" | "n_neighbors" | "min_dist" | "pca_x" | "pca_y" | "perplexity"
>;

const UMAP: Params = { method: "umap", n_neighbors: 15, min_dist: 0.1 };

describe("ProjectionControls", () => {
  it("UMAP shows n_neighbors + min_dist sliders", () => {
    render(<ProjectionControls value={UMAP} onChange={vi.fn()} onApply={vi.fn()} />);
    expect(screen.getByLabelText(/n_neighbors/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/min_dist/i)).toBeInTheDocument();
    // No PCA / t-SNE params while UMAP is active.
    expect(screen.queryByLabelText(/主成分 X/)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/perplexity/i)).not.toBeInTheDocument();
  });

  it("switching to PCA shows the 主成分 X / Y dropdowns", async () => {
    const onChange = vi.fn();
    const { rerender } = render(<ProjectionControls value={UMAP} onChange={onChange} onApply={vi.fn()} />);

    await userEvent.click(screen.getByRole("button", { name: /^PCA$/ }));
    // Method change flows up.
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ method: "pca" }));

    // Re-render in PCA mode (the parent applied the method change) to show its conditional params.
    rerender(<ProjectionControls value={{ method: "pca", pca_x: 0, pca_y: 1 }} onChange={onChange} onApply={vi.fn()} />);
    expect(screen.getByLabelText(/主成分 X/)).toBeInTheDocument();
    expect(screen.getByLabelText(/主成分 Y/)).toBeInTheDocument();
    expect(screen.queryByLabelText(/n_neighbors/i)).not.toBeInTheDocument();
  });

  it("t-SNE shows a perplexity slider and a 较慢 hint", async () => {
    const onChange = vi.fn();
    const { rerender } = render(<ProjectionControls value={UMAP} onChange={onChange} onApply={vi.fn()} />);

    await userEvent.click(screen.getByRole("button", { name: /t-SNE/i }));
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ method: "tsne" }));

    rerender(<ProjectionControls value={{ method: "tsne", perplexity: 30 }} onChange={onChange} onApply={vi.fn()} />);
    expect(screen.getByLabelText(/perplexity/i)).toBeInTheDocument();
    expect(screen.getByText(/较慢/)).toBeInTheDocument();
  });

  it("dragging a slider updates params but does NOT auto-apply", async () => {
    const onChange = vi.fn();
    const onApply = vi.fn();
    render(<ProjectionControls value={UMAP} onChange={onChange} onApply={onApply} />);

    fireEvent.change(screen.getByLabelText(/n_neighbors/i), { target: { value: "30" } });
    // The change flows to the parent state...
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ n_neighbors: 30 }));
    // ...but no refetch is triggered.
    expect(onApply).not.toHaveBeenCalled();
  });

  it("clicking 投射 calls onApply", async () => {
    const onApply = vi.fn();
    render(<ProjectionControls value={UMAP} onChange={vi.fn()} onApply={onApply} />);

    await userEvent.click(screen.getByRole("button", { name: /投射/ }));
    expect(onApply).toHaveBeenCalledTimes(1);
  });

  it("shows a capped note when last result was subsampled", () => {
    render(
      <ProjectionControls value={UMAP} onChange={vi.fn()} onApply={vi.fn()} capped n={2000} total={5000} />
    );
    expect(screen.getByText(/已采样/)).toHaveTextContent("2000");
    expect(screen.getByText(/已采样/)).toHaveTextContent("5000");
  });
});
