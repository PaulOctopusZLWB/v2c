import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState, type ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ScopeSelector, type Scope } from "../features/viz/ScopeSelector";

function dateOffset(days: number) {
  const date = new Date(); date.setDate(date.getDate() + days);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}
const recent = dateOffset(-1);
const older = dateOffset(-8);
const empty: Scope = { session_ids: [], days: [] };

function mockFetch() {
  return vi.fn(async (url: string) => {
    const path = String(url).split("?")[0];
    if (path === "/api/transcripts/days") return new Response(JSON.stringify({ days: [
      { day: recent, session_count: 2 }, { day: older, session_count: 1 }
    ] }), { status: 200 });
    if (path === `/api/transcripts/days/${recent}/sessions`) return new Response(JSON.stringify({ sessions: [
      { session_id: "ses_a", started_at: `${recent}T09:30:00+08:00`, segment_count: 12 },
      { session_id: "ses_b", started_at: `${recent}T14:05:00+08:00`, segment_count: 7 }
    ] }), { status: 200 });
    if (path === `/api/transcripts/days/${older}/sessions`) return new Response(JSON.stringify({ sessions: [
      { session_id: "ses_old", started_at: `${older}T09:30:00+08:00`, segment_count: 4 }
    ] }), { status: 200 });
    return new Response("{}", { status: 200 });
  });
}
function renderWithQuery(ui: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}
function Controlled({ initial = empty, onChange = () => undefined }: { initial?: Scope; onChange?: (value: Scope) => void }) {
  const [value, setValue] = useState(initial);
  return <ScopeSelector value={value} onChange={(next) => { setValue(next); onChange(next); }} />;
}

describe("ScopeSelector", () => {
  beforeEach(() => vi.stubGlobal("fetch", mockFetch()));
  afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

  it("shows recent dates and folds older dates without losing selections", async () => {
    renderWithQuery(<Controlled />);
    expect(await screen.findByRole("checkbox", { name: new RegExp(recent) })).toBeInTheDocument();
    expect(screen.queryByRole("checkbox", { name: new RegExp(older) })).not.toBeInTheDocument();
    await userEvent.click(screen.getByText(/更早日期/));
    await userEvent.click(await screen.findByRole("checkbox", { name: new RegExp(older) }));
    await userEvent.click(screen.getByText(/更早日期/));
    await userEvent.click(screen.getByText(/更早日期/));
    expect(await screen.findByRole("checkbox", { name: new RegExp(older) })).toBeChecked();
  });

  it("whole day and individual sessions stay consistent, including partial selection", async () => {
    const changed = vi.fn(); renderWithQuery(<Controlled onChange={changed} />);
    const day = await screen.findByRole("checkbox", { name: new RegExp(recent) });
    await userEvent.click(day);
    await waitFor(() => expect(changed).toHaveBeenLastCalledWith({ days: [recent], session_ids: [] }));
    await userEvent.click(screen.getByRole("button", { name: `展开 ${recent}` }));
    const first = await screen.findByRole("checkbox", { name: /09:30/ });
    expect(first).toBeChecked();
    await userEvent.click(first);
    expect(changed).toHaveBeenLastCalledWith({ days: [], session_ids: ["ses_b"] });
    expect(day).toBePartiallyChecked();
    await userEvent.click(day);
    expect(changed).toHaveBeenLastCalledWith({ days: [recent], session_ids: [] });
  });

  it("day selection removes redundant selected sessions even before expansion", async () => {
    const changed = vi.fn(); renderWithQuery(<Controlled initial={{ days: [], session_ids: ["ses_a"] }} onChange={changed} />);
    await userEvent.click(await screen.findByRole("checkbox", { name: new RegExp(recent) }));
    await waitFor(() => expect(changed).toHaveBeenLastCalledWith({ days: [recent], session_ids: [] }));
  });

  it("marks an externally selected session as a partial day before expansion", async () => {
    renderWithQuery(<ScopeSelector value={{ days: [], session_ids: ["ses_a"] }} activeSessionId="ses_a" activeSessionDay={recent} onChange={vi.fn()} />);
    expect(await screen.findByRole("checkbox", { name: new RegExp(recent) })).toBePartiallyChecked();
  });

  it("does not restore a day after the user clears while its sessions are loading", async () => {
    let resolve!: (response: Response) => void;
    const original = mockFetch();
    vi.stubGlobal("fetch", vi.fn((url: string) => String(url).includes(`/days/${older}/sessions`)
      ? new Promise<Response>((done) => { resolve = done; }) : original(url)));
    const changed = vi.fn(); renderWithQuery(<Controlled initial={{ days: [], session_ids: ["ses_a"] }} onChange={changed} />);
    await userEvent.click(await screen.findByText(/更早日期/));
    await userEvent.click(await screen.findByRole("checkbox", { name: new RegExp(older) }));
    await userEvent.click(screen.getByRole("button", { name: "清空" }));
    resolve(new Response(JSON.stringify({ sessions: [{ session_id: "ses_old", started_at: `${older}T09:30:00+08:00`, segment_count: 4 }] }), { status: 200 }));
    await waitFor(() => expect(screen.getByRole("checkbox", { name: new RegExp(older) })).toBeEnabled());
    expect(changed).toHaveBeenLastCalledWith({ days: [], session_ids: [] });
    expect(changed).toHaveBeenCalledTimes(1);
  });
});
