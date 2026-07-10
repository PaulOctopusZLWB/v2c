import type { DayStatusRow } from "../api/types";

export type DayRow = { day: string; session_count: number };

export function mergeDaysWithStatus(days: DayRow[], dayStatus: DayStatusRow[]): DayRow[] {
  const byDay = new Map(days.map((day) => [day.day, { ...day }]));
  for (const status of dayStatus) {
    if (!byDay.has(status.day)) {
      byDay.set(status.day, { day: status.day, session_count: status.session_count });
    }
  }
  return Array.from(byDay.values()).sort((a, b) => b.day.localeCompare(a.day));
}
