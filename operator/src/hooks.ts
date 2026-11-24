import { useEffect, useState } from "react";
import { api } from "./types";
export function useData<T>(
  url: string | null,
  interval = 0,
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
      try {
        const value = await api<T>(url!, { signal: ctl.signal });
        if (!ctl.signal.aborted) setResult({ url: url!, value });
      } catch {
        if (!ctl.signal.aborted)
          setResult((r) => ({
            url: url!,
            value: r.url === url ? r.value : undefined,
            error: "ارتباط با سرویس برقرار نشد.",
          }));
      }
      if (!ctl.signal.aborted && interval)
        timer = window.setTimeout(load, interval);
    }
    void load();
    return () => {
      ctl.abort();
      clearTimeout(timer);
    };
  }, [url, interval]);
  return result.url === url ? result : { url };
}
