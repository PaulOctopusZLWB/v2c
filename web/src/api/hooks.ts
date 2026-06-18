import { useQuery } from "@tanstack/react-query";
import { api } from "./client";
import { queryKeys } from "./queryKeys";
import type { PersonRow } from "./types";

export function useDaysQuery() {
  return useQuery({
    queryKey: queryKeys.days(),
    queryFn: () => api.days()
  });
}

export function useDayStatusQuery() {
  return useQuery({
    queryKey: queryKeys.dayStatus(),
    queryFn: () => api.dayStatus()
  });
}

export function useSessionsForDayQuery(day: string | null | undefined) {
  return useQuery({
    queryKey: queryKeys.sessionsForDay(day ?? ""),
    queryFn: () => api.sessionsForDay(day ?? ""),
    enabled: !!day
  });
}

export function usePeopleQuery() {
  return useQuery<{ people: PersonRow[] }>({
    queryKey: queryKeys.people(),
    queryFn: () => api.people()
  });
}

export function useHomeOverviewQuery() {
  return useQuery({
    queryKey: queryKeys.homeOverview(),
    queryFn: () => api.homeOverview()
  });
}

export function useEmbeddingStatusQuery(scope: { session_id?: string | null; day?: string | null }) {
  return useQuery({
    queryKey: queryKeys.embeddingStatus(scope),
    queryFn: () => api.embeddingStatus(scope)
  });
}
