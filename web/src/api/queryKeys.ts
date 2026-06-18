export const queryKeys = {
  days: () => ["days"] as const,
  dayStatus: () => ["dayStatus"] as const,
  sessionsForDay: (day: string) => ["sessionsForDay", day] as const,
  people: () => ["people"] as const,
  persons: () => ["persons"] as const,
  homeOverview: () => ["homeOverview"] as const,
  embeddingStatus: (scope: { session_id?: string | null; day?: string | null }) =>
    ["embeddingStatus", scope.session_id ?? null, scope.day ?? null] as const
};
