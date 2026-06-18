import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useDaysQuery, usePeopleQuery, useSessionsForDayQuery } from "../api/hooks";
import { queryKeys } from "../api/queryKeys";

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

describe("api query hooks", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("uses stable query keys", () => {
    expect(queryKeys.days()).toEqual(["days"]);
    expect(queryKeys.sessionsForDay("2087-05-10")).toEqual(["sessionsForDay", "2087-05-10"]);
    expect(queryKeys.people()).toEqual(["people"]);
  });

  it("loads days through useDaysQuery", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url === "/api/transcripts/days") {
          return new Response(JSON.stringify({ days: [{ day: "2087-05-10", session_count: 2 }] }), { status: 200 });
        }
        return new Response("{}", { status: 200 });
      })
    );

    const { result } = renderHook(() => useDaysQuery(), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.days[0].day).toBe("2087-05-10");
  });

  it("does not load sessions without a day", () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 200 })));
    const { result } = renderHook(() => useSessionsForDayQuery(""), { wrapper });
    expect(result.current.fetchStatus).toBe("idle");
    expect(fetch).not.toHaveBeenCalled();
  });

  it("loads people through usePeopleQuery", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url === "/api/people") {
          return new Response(
            JSON.stringify({
              people: [
                {
                  person_id: "per_1",
                  display_name: "吴博",
                  person_type: "contact",
                  is_self: 0,
                  enrolled: true,
                  attributed_count: 3,
                  manual_count: 2
                }
              ]
            }),
            { status: 200 }
          );
        }
        return new Response("{}", { status: 200 });
      })
    );

    const { result } = renderHook(() => usePeopleQuery(), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.people[0].display_name).toBe("吴博");
  });
});
