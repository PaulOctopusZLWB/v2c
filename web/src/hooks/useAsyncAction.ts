import { useCallback, useState } from "react";

/**
 * Wraps an async function and tracks an in-flight flag so callers can render
 * pending/disabled UI without bookkeeping. The wrapped `run` keeps the original
 * argument signature.
 */
export function useAsyncAction<A extends unknown[]>(fn: (...args: A) => Promise<unknown>) {
  const [pending, setPending] = useState(false);
  const run = useCallback(
    async (...args: A) => {
      setPending(true);
      try {
        await fn(...args);
      } finally {
        setPending(false);
      }
    },
    [fn]
  );
  return { run, pending };
}
