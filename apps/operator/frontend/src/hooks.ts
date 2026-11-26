import { useEffect, useState } from "react";
import { api } from "./types";
export function useData<T>(
  url: string | null,
  interval = 0,
  repeat?: (value?: T) => boolean,
): { url: string | null; value?: T; error?: string } {
  const [result, setResult] = useState<{
    url: string;
    value?: T;
    error?: string;
  }>({ url: "" });
  useEffect(() => {
    if (!url) return;
    const ctl = new AbortController();
    let timer = 0;
    async function load() {
      let value: T | undefined;
      try {
        value = await api<T>(url!, { signal: ctl.signal });
        if (!ctl.signal.aborted) setResult({ url: url!, value });
      } catch {
        if (!ctl.signal.aborted)
          setResult((r) => ({
            url: url!,
            value: r.url === url ? r.value : undefined,
            error: "ارتباط با سرویس برقرار نشد.",
          }));
      }
      if (!ctl.signal.aborted && interval && (!repeat || repeat(value)))
        timer = window.setTimeout(load, interval);
    }
    void load();
    return () => {
      ctl.abort();
      clearTimeout(timer);
    };
  }, [url, interval, repeat]);
  return result.url === url ? result : { url };
}

export function useMedia(query: string) {
  const [matches, setMatches] = useState(
    () => window.matchMedia(query).matches,
  );
  useEffect(() => {
    const mq = window.matchMedia(query);
    const update = () => setMatches(mq.matches);
    update();
    mq.addEventListener("change", update);
    return () => mq.removeEventListener("change", update);
  }, [query]);
  return matches;
}
