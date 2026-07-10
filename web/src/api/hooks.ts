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

/** 管道控制室「阶段耗时」面板:按 task_type 的成功率 + 时长分位数。 */
export function usePipelineMetricsQuery() {
  return useQuery({
    queryKey: queryKeys.pipelineMetrics(),
    queryFn: () => api.pipelineMetrics()
  });
}
