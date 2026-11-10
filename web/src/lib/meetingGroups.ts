import type { MeetingGroup } from "../api/types";

export type GroupedSession<T> = { session: T; group?: MeetingGroup; members: T[] };

/** Only collapse complete groups whose every source is in the response. */
export function groupSessions<T extends { session_id: string }>(sessions: T[], groups: MeetingGroup[]): GroupedSession<T>[] {
  const byId = new Map(sessions.map((session) => [session.session_id, session]));
  const byMember = new Map<string, MeetingGroup>();
  for (const group of groups) {
    if (group.session_ids.length < 2 || group.session_ids.some((id) => !byId.has(id) || byMember.has(id))) continue;
    group.session_ids.forEach((id) => byMember.set(id, group));
  }
  const emitted = new Set<string>();
  const result: GroupedSession<T>[] = [];
  for (const session of sessions) {
    const group = byMember.get(session.session_id);
    if (!group) { result.push({ session, members: [session] }); continue; }
    if (emitted.has(group.group_id)) continue;
    emitted.add(group.group_id);
    result.push({ session, group, members: group.session_ids.map((id) => byId.get(id)!) });
  }
  return result;
}
