import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import type { ViewpointContent } from "../api/types";

/** Capture the URL, method and parsed body of the single fetch each api method makes. */
function captureFetch(response: unknown = {}) {
  const spy = vi.fn(async () => new Response(JSON.stringify(response), { status: 200 }));
  vi.stubGlobal("fetch", spy);
  return spy;
}

function call(spy: ReturnType<typeof vi.fn>) {
  const [url, init] = spy.mock.calls[0] as [string, RequestInit | undefined];
  return {
    url,
    method: init?.method ?? "GET",
    body: init?.body ? JSON.parse(String(init.body)) : undefined
  };
}

const content: ViewpointContent = {
  headline: "本会话观点",
  summary: "讨论了部署方案。",
  topics: ["部署", "安全"],
  decisions: [{ text: "采用本地部署", evidence_refs: ["seg_1"] }],
  todos: [{ text: "准备机器", owner: "Paul", evidence_refs: ["seg_2"] }],
  open_questions: ["预算是多少?"],
  core_conclusions: ["数据不出本机"],
  per_speaker: [
    {
      speaker_cluster_id: "spk_1",
      viewpoints: [{ text: "我支持本地部署", evidence_refs: ["seg_1"] }],
      sentiment: "positive",
      stance: "支持",
      latent_needs: ["数据安全"]
    }
  ]
};

describe("viewpoint api client", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("viewpoint(id) GETs the session viewpoint state", async () => {
    const spy = captureFetch({ session_id: "ses_1" });
    await api.viewpoint("ses_1");
    const c = call(spy);
    expect(c.url).toBe("/api/sessions/ses_1/viewpoint");
    expect(c.method).toBe("GET");
  });

  it("editSegmentText PATCHes the segment text", async () => {
    const spy = captureFetch({ segment_id: "seg_1", text: "改过的文本" });
    await api.editSegmentText("seg_1", "改过的文本");
    const c = call(spy);
    expect(c.url).toBe("/api/transcripts/segments/seg_1");
    expect(c.method).toBe("PATCH");
    expect(c.body).toEqual({ text: "改过的文本" });
  });

  it("generateViewpoint POSTs to the generate endpoint", async () => {
    const spy = captureFetch({ enqueued: true, session_id: "ses_1" });
    await api.generateViewpoint("ses_1");
    const c = call(spy);
    expect(c.url).toBe("/api/sessions/ses_1/viewpoint/generate");
    expect(c.method).toBe("POST");
  });

  it("editViewpoint PUTs the full content doc", async () => {
    const spy = captureFetch({ session_id: "ses_1" });
    await api.editViewpoint("ses_1", content);
    const c = call(spy);
    expect(c.url).toBe("/api/sessions/ses_1/viewpoint");
    expect(c.method).toBe("PUT");
    expect(c.body).toEqual({ content });
  });

  it("clearViewpointEdit DELETEs the edit", async () => {
    const spy = captureFetch({ session_id: "ses_1" });
    await api.clearViewpointEdit("ses_1");
    const c = call(spy);
    expect(c.url).toBe("/api/sessions/ses_1/viewpoint/edit");
    expect(c.method).toBe("DELETE");
  });

  it("publishViewpoint POSTs to publish", async () => {
    const spy = captureFetch({ note_path: "x.md", published_at: "2026-06-18" });
    await api.publishViewpoint("ses_1");
    const c = call(spy);
    expect(c.url).toBe("/api/sessions/ses_1/viewpoint/publish");
    expect(c.method).toBe("POST");
  });

  it("getSessionPrompt GETs the session_viewpoint prompt", async () => {
    const spy = captureFetch({ template: "tmpl", default: "def" });
    await api.getSessionPrompt();
    const c = call(spy);
    expect(c.url).toBe("/api/prompts/session_viewpoint");
    expect(c.method).toBe("GET");
  });

  it("setSessionPrompt PUTs the global template", async () => {
    const spy = captureFetch({ template: "tmpl", default: "def" });
    await api.setSessionPrompt("新模板");
    const c = call(spy);
    expect(c.url).toBe("/api/prompts/session_viewpoint");
    expect(c.method).toBe("PUT");
    expect(c.body).toEqual({ template: "新模板" });
  });

  it("setSessionPromptOverride PUTs the per-session template (and null to clear)", async () => {
    const spy = captureFetch({ effective: "x", default: "y", is_override: true });
    await api.setSessionPromptOverride("ses_1", "覆盖模板");
    expect(call(spy)).toEqual({
      url: "/api/sessions/ses_1/viewpoint/prompt",
      method: "PUT",
      body: { template: "覆盖模板" }
    });

    vi.unstubAllGlobals();
    const spy2 = captureFetch({ effective: "y", default: "y", is_override: false });
    await api.setSessionPromptOverride("ses_1", null);
    expect(call(spy2)).toEqual({
      url: "/api/sessions/ses_1/viewpoint/prompt",
      method: "PUT",
      body: { template: null }
    });
  });

  it("surfaces a 400 validation message from editViewpoint", async () => {
    const spy = vi.fn(async () =>
      new Response(JSON.stringify({ detail: "evidence_refs 越界" }), { status: 400 })
    );
    vi.stubGlobal("fetch", spy);
    await expect(api.editViewpoint("ses_1", content)).rejects.toThrow(/evidence_refs 越界/);
  });
});
