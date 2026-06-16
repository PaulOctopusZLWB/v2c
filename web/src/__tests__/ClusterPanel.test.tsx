import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ClusterPanel } from "../components/ClusterPanel";
import type { Person, SpeakerCluster } from "../api/types";

const clusters: SpeakerCluster[] = [
  {
    speaker_cluster_id: "spk_00",
    person_id: null,
    person_label: null,
    segment_count: 12,
    total_speech_ms: 60000,
    sample_segment_id: "seg_a",
    sample_text: "今天我们聊聊产品"
  },
  {
    speaker_cluster_id: "spk_01",
    person_id: null,
    person_label: null,
    segment_count: 7,
    total_speech_ms: 30000,
    sample_segment_id: "seg_b",
    sample_text: "嗯我同意这个方案"
  }
];

const persons: Person[] = [{ person_id: "per_paul", display_name: "Paul", person_type: "self", is_self: 1 }];

function mockFetch(impl: (url: string, init?: RequestInit) => Response | Promise<Response>) {
  vi.stubGlobal("fetch", vi.fn(impl));
}

describe("ClusterPanel", () => {
  beforeEach(() => {
    mockFetch(async (url) => {
      if (url.startsWith("/api/speakers/clusters")) return new Response(JSON.stringify({ clusters }), { status: 200 });
      if (url === "/api/speakers/assign-person-bulk") return new Response(JSON.stringify({ assigned: 2 }), { status: 200 });
      return new Response("{}", { status: 200 });
    });
  });
  afterEach(() => vi.unstubAllGlobals());

  it("lists the day's clusters with sample text and segment counts", async () => {
    render(<ClusterPanel day="2026-06-15" persons={persons} onCreatePerson={async () => undefined} />);
    expect(await screen.findByText("今天我们聊聊产品")).toBeInTheDocument();
    expect(screen.getByText("嗯我同意这个方案")).toBeInTheDocument();
    expect(screen.getByText(/12/)).toBeInTheDocument();
    expect(screen.getByText(/7/)).toBeInTheDocument();
  });

  it("merges selected clusters into one person via POST /api/speakers/assign-person-bulk", async () => {
    render(<ClusterPanel day="2026-06-15" persons={persons} onCreatePerson={async () => undefined} />);
    await screen.findByText("今天我们聊聊产品");

    // Select both clusters (the per-row selection checkbox).
    await userEvent.click(screen.getByRole("checkbox", { name: "spk_00" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "spk_01" }));
    // Pick a person.
    await userEvent.selectOptions(screen.getByLabelText(/选择人物/), "per_paul");
    // Merge.
    await userEvent.click(screen.getByRole("button", { name: /合并\/指派为同一人/ }));

    const post = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.find(
      (c) => c[0] === "/api/speakers/assign-person-bulk"
    );
    expect(post).toBeTruthy();
    const body = JSON.parse((post![1] as RequestInit).body as string);
    expect(body.speakers).toEqual(["spk_00", "spk_01"]);
    expect(body.person_id).toBe("per_paul");

    // The list is refreshed after a successful merge (a second GET).
    await waitFor(() => {
      const getCalls = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.filter((c) =>
        (c[0] as string).startsWith("/api/speakers/clusters")
      );
      expect(getCalls.length).toBeGreaterThanOrEqual(2);
    });
  });
});
