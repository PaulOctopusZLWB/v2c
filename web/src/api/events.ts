import type { StatusSnapshot } from "./types";

export function subscribeStatus(onSnapshot: (snap: StatusSnapshot) => void): () => void {
  const source = new EventSource("/api/events");
  source.addEventListener("status.snapshot", (event) => onSnapshot(JSON.parse((event as MessageEvent).data) as StatusSnapshot));
  return () => source.close();
}
