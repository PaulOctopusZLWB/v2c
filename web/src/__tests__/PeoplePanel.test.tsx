import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PeoplePanel } from "../features/people/PeoplePanel";
import type { PersonRow } from "../api/types";

const people: PersonRow[] = [
  { person_id: "per_a", display_name: "韩文巧", person_type: "contact", is_self: 0, enrolled: true, attributed_count: 12, manual_count: 4 },
  { person_id: "per_b", display_name: "李雷", person_type: "contact", is_self: 0, enrolled: false, attributed_count: 0, manual_count: 0 }
];

/** Drive the People-panel endpoints; everything unmatched is `{}`. */
function mockFetch(overrides: Record<string, unknown> = {}) {
  const body: Record<string, unknown> = {
    "/api/people": { people },
    ...overrides
  };
  return vi.fn(async (url: string, init?: RequestInit) => {
    const path = String(url).split("?")[0];
    if (path === "/api/people" && (!init || init.method === undefined || init.method === "GET"))
      return new Response(JSON.stringify(body["/api/people"]), { status: 200 });
    if (path in body && path !== "/api/people")
      return new Response(JSON.stringify(body[path]), { status: 200 });
    return new Response("{}", { status: 200 });
  });
}

const noop = () => {};

describe("PeoplePanel", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("renders people with an enrolled badge plus manual + attributed counts", async () => {
    vi.stubGlobal("fetch", mockFetch());
    render(<PeoplePanel sessionId={null} day={null} onChanged={noop} push={noop} pushAction={noop} />);

    expect(await screen.findByText("韩文巧")).toBeInTheDocument();
    expect(screen.getByText("李雷")).toBeInTheDocument();
    // enrolled person shows the ✓ badge + both its manual (ground-truth) and attributed counts.
    const hanRow = screen.getByText("韩文巧").closest(".person-row") as HTMLElement;
    expect(hanRow.querySelector(".person-enrolled")).toBeTruthy();
    expect(hanRow.textContent).toContain("4"); // manual_count
    expect(hanRow.textContent).toContain("12"); // attributed_count
    const leiRow = screen.getByText("李雷").closest(".person-row") as HTMLElement;
    expect(leiRow.querySelector(".person-enrolled")).toBeFalsy();
  });

  it("disables 登记声纹 for a person with no manual labels, enables it when labelled", async () => {
    vi.stubGlobal("fetch", mockFetch());
    render(<PeoplePanel sessionId={null} day={null} onChanged={noop} push={noop} pushAction={noop} />);

    // 李雷 has manual_count 0 → can't enroll (the 400 the user hit). Button is disabled.
    const leiRow = (await screen.findByText("李雷")).closest(".person-row") as HTMLElement;
    expect(within(leiRow).getByRole("button", { name: /登记声纹/ })).toBeDisabled();
    // 韩文巧 has manual_count 4 → enroll button is enabled.
    const hanRow = screen.getByText("韩文巧").closest(".person-row") as HTMLElement;
    expect(within(hanRow).getByRole("button", { name: /登记声纹/ })).not.toBeDisabled();
  });

  it("clicking 登记声纹 calls enrollPerson for a labelled person", async () => {
    vi.stubGlobal("fetch", mockFetch({ "/api/people/per_a/enroll": { person_id: "per_a", n_segments: 8, dim: 192 } }));
    const onChanged = vi.fn();
    render(<PeoplePanel sessionId={null} day={null} onChanged={onChanged} push={noop} pushAction={noop} />);

    const hanRow = (await screen.findByText("韩文巧")).closest(".person-row") as HTMLElement;
    await userEvent.click(within(hanRow).getByRole("button", { name: /登记声纹/ }));

    await waitFor(() => {
      const calls = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls;
      const enroll = calls.find((c) => String(c[0]) === "/api/people/per_a/enroll");
      expect(enroll).toBeTruthy();
      expect((enroll![1] as RequestInit).method).toBe("POST");
    });
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });

  it("智能建议 with a session fetches suggestions and renders them with a confidence chip", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch({
        "/api/speakers/suggest": {
          suggestions: [{ speaker: "spk_01", person_id: "per_a", person_label: "韩文巧", score: 0.82 }]
        }
      })
    );
    render(<PeoplePanel sessionId="ses_1" day={null} onChanged={noop} push={noop} pushAction={noop} />);

    await screen.findByText("韩文巧"); // people loaded
    await userEvent.click(screen.getByRole("button", { name: /智能建议/ }));

    expect(await screen.findByText(/spk_01/)).toBeInTheDocument();
    // the suggested person + a confidence chip showing the score.
    const row = screen.getByText(/spk_01/).closest(".suggestion-row") as HTMLElement;
    expect(row.textContent).toContain("韩文巧");
    expect(row.querySelector(".confidence-chip")?.textContent).toContain("0.82");
  });

  it("采用 on a suggestion fetches that speaker's segments then labels them for the person", async () => {
    const segments = [
      { segment_id: "seg_1", text: "一", speaker: "spk_01", absolute_start_at: null, has_embedding: true },
      { segment_id: "seg_2", text: "二", speaker: "spk_01", absolute_start_at: null, has_embedding: true }
    ];
    vi.stubGlobal(
      "fetch",
      mockFetch({
        "/api/speakers/suggest": {
          suggestions: [{ speaker: "spk_01", person_id: "per_a", person_label: "韩文巧", score: 0.82 }]
        },
        "/api/speakers/segments": { segments },
        "/api/people/per_a/label-segments": { labeled: 2 }
      })
    );
    const onChanged = vi.fn();
    render(<PeoplePanel sessionId="ses_1" day={null} onChanged={onChanged} push={noop} pushAction={noop} />);

    await screen.findByText("韩文巧");
    await userEvent.click(screen.getByRole("button", { name: /智能建议/ }));
    await screen.findByText(/spk_01/);

    await userEvent.click(screen.getByRole("button", { name: /采用/ }));

    await waitFor(() => {
      const calls = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.map((c) => ({ url: String(c[0]), init: c[1] as RequestInit | undefined }));
      // fetched the cluster's segments scoped to the session + speaker...
      const segCall = calls.find((c) => c.url.startsWith("/api/speakers/segments"));
      expect(segCall).toBeTruthy();
      expect(segCall!.url).toContain("session_id=ses_1");
      expect(segCall!.url).toContain("speaker=spk_01");
      // ...then labelled those segment ids for the suggested person.
      const labelCall = calls.find((c) => c.url === "/api/people/per_a/label-segments");
      expect(labelCall).toBeTruthy();
      expect(JSON.parse(String(labelCall!.init!.body)).segment_ids).toEqual(["seg_1", "seg_2"]);
    });
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });

  it("全局识别 defaults to scope 全部 → calls auto-attribute with session_id null (global)", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch({
        "/api/people/auto-attribute": { assigned: 5, unassigned: 1, total: 6, per_person: { per_a: 5 }, threshold: 0.6 }
      })
    );
    const onChanged = vi.fn();
    // even with a session selected, the default scope is 全部 (cross-session identity).
    render(<PeoplePanel sessionId="ses_1" day={null} onChanged={onChanged} push={noop} pushAction={noop} />);

    await screen.findByText("韩文巧");
    await userEvent.click(screen.getByRole("button", { name: /全局识别/ }));

    await waitFor(() => {
      const calls = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.map((c) => ({ url: String(c[0]), init: c[1] as RequestInit | undefined }));
      const auto = calls.find((c) => c.url === "/api/people/auto-attribute");
      expect(auto).toBeTruthy();
      const sent = JSON.parse(String(auto!.init!.body));
      expect(sent.session_id).toBeNull();
      expect(sent.day).toBeNull();
    });
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });

  it("switching 全局识别 scope to 本会话 passes the selected session_id", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch({
        "/api/people/auto-attribute": { assigned: 5, unassigned: 1, total: 6, per_person: { per_a: 5 }, threshold: 0.6 }
      })
    );
    render(<PeoplePanel sessionId="ses_1" day={null} onChanged={noop} push={noop} pushAction={noop} />);

    await screen.findByText("韩文巧");
    // flip the scope to 本会话, then run.
    await userEvent.click(screen.getByRole("radio", { name: /本会话/ }));
    await userEvent.click(screen.getByRole("button", { name: /全局识别/ }));

    await waitFor(() => {
      const calls = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.map((c) => ({ url: String(c[0]), init: c[1] as RequestInit | undefined }));
      const auto = calls.find((c) => c.url === "/api/people/auto-attribute");
      expect(auto).toBeTruthy();
      expect(JSON.parse(String(auto!.init!.body)).session_id).toBe("ses_1");
    });
  });

  it("新建人物 creates a person then reloads the list", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch({ "/api/persons": { person_id: "per_c", display_name: "王芳", person_type: "other", is_self: 0 } })
    );
    render(<PeoplePanel sessionId={null} day={null} onChanged={noop} push={noop} pushAction={noop} />);

    await screen.findByText("韩文巧");
    await userEvent.type(screen.getByLabelText("新建人物"), "王芳");
    await userEvent.click(screen.getByRole("button", { name: /新建/ }));

    await waitFor(() => {
      const calls = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.map((c) => ({ url: String(c[0]), init: c[1] as RequestInit | undefined }));
      const create = calls.find((c) => c.url === "/api/persons" && c.init?.method === "POST");
      expect(create).toBeTruthy();
      expect(JSON.parse(String(create!.init!.body)).display_name).toBe("王芳");
    });
  });
});
