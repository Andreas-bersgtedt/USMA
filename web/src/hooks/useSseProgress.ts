import { useEffect, useState } from "react";

export type SseEvent = { type: string; [k: string]: unknown };

/** Latest progress snapshot for one module. */
export type ModuleProgress = {
  current: number;
  total: number;
  label?: string;
  message?: string | null;
};

/**
 * Subscribe to /api/runs/<id>/events. Returns the latest event list, a
 * per-module progress map (latest `module_progress` snapshot), and a
 * `done` flag once the stream terminates.
 */
export function useSseProgress(runId: string | null): {
  events: SseEvent[];
  progressByModule: Record<string, ModuleProgress>;
  done: boolean;
  error: string | null;
} {
  const [events, setEvents] = useState<SseEvent[]>([]);
  const [progressByModule, setProgressByModule] = useState<
    Record<string, ModuleProgress>
  >({});
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setEvents([]);
    setProgressByModule({});
    setDone(false);
    setError(null);
    if (!runId) return;
    const url = `/api/runs/${encodeURIComponent(runId)}/events`;
    const es = new EventSource(url);
    const onMessage = (ev: MessageEvent) => {
      try {
        const data = JSON.parse(ev.data) as SseEvent;
        setEvents((prev) => [...prev, data]);
        if (data.type === "module_progress") {
          const mod = String(data.module ?? "");
          if (mod) {
            setProgressByModule((prev) => ({
              ...prev,
              [mod]: {
                current: Number(data.current ?? 0),
                total: Number(data.total ?? 0),
                label: data.label as string | undefined,
                message: (data.message as string | null | undefined) ?? null,
              },
            }));
          }
        }
        if (data.type === "done") {
          setDone(true);
          es.close();
        }
      } catch {
        /* ignore */
      }
    };
    // FastAPI sse-starlette uses event names; listen to the catch-all.
    es.onmessage = onMessage;
    for (const name of [
      "run_started",
      "module_started",
      "module_progress",
      "module_finished",
      "done",
      "ping",
    ]) {
      es.addEventListener(name, onMessage as unknown as EventListener);
    }
    es.onerror = () => {
      setError("SSE connection lost");
      es.close();
    };
    return () => es.close();
  }, [runId]);

  return { events, progressByModule, done, error };
}
