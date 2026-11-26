import { recordingMoment, routeUrl } from "./navigation";
import { useData } from "./hooks";
import { date, fa, Link, Route } from "./types";

const verdicts: Record<string, string> = {
  matched: "تطبیق با مرجع",
  unknown: "ناشناخته",
  pending: "در انتظار بررسی",
  poor_quality: "کیفیت پایین",
  error: "خطای پردازش",
};
type Hit = {
  detection_id: string;
  occurrence_id: string;
  frame_index: number;
  source_id: string;
  observed_at: number;
  state: string;
  name: string | null;
  version: number | null;
  score: number | null;
};
export function Detections({ route, go }: { route: Route; go: Link }) {
  const params = new URLSearchParams();
  if (route.source) params.set("source", route.source);
  if (route.status) params.set("state", route.status);
  if (route.after) params.set("before", route.after);
  const list = useData<{
    items: Hit[];
    sources: string[];
    next_cursor: number | null;
  }>("/api/operator/detections?" + params, route.after ? 0 : 5000);
  return (
    <>
      <h1>چهره‌های دیده‌شده</h1>
      <div className="row">
        <label>
          دوربین{" "}
          <select
            value={route.source || ""}
            onChange={(e) =>
              go({ ...route, source: e.target.value, after: undefined })
            }
          >
            <option value="">همه</option>
            {list.value?.sources.map((s) => (
              <option key={s}>{s}</option>
            ))}
          </select>
        </label>
        <label>
          نتیجه{" "}
          <select
            value={route.status || ""}
            onChange={(e) =>
              go({ ...route, status: e.target.value, after: undefined })
            }
          >
            <option value="">همه</option>
            {Object.entries(verdicts).map(([k, v]) => (
              <option value={k} key={k}>
                {v}
              </option>
            ))}
          </select>
        </label>
      </div>
      {list.error && <p role="alert">{list.error}</p>}
      {list.value?.items.length === 0 && (
        <p>چهره‌ای با این فیلتر ثبت نشده است.</p>
      )}
      <div className="detections-grid">
        {list.value?.items.map((h) => (
          <a
            className="card"
            key={h.detection_id}
            href={routeUrl(recordingMoment(h, h.source_id))}
          >
            <img
              loading="lazy"
              src={"/api/operator/crops/" + encodeURIComponent(h.detection_id)}
              alt="چهره دیده‌شده"
            />
            <strong>
              {verdicts[h.state] || h.state}
              {h.name && " · " + h.name}
            </strong>
            <span>
              {h.source_id} · {date(h.observed_at)}
            </span>
            {h.score !== null && (
              <span>
                امتیاز شباهت{" "}
                {h.score.toLocaleString("fa-IR", { maximumFractionDigits: 3 })}{" "}
                · نسخه {fa(h.version ?? 0)}
              </span>
            )}
          </a>
        ))}
      </div>
      {list.value?.next_cursor != null && (
        <button
          onClick={() =>
            go({ ...route, after: String(list.value!.next_cursor) })
          }
        >
          قدیمی‌تر
        </button>
      )}{" "}
      {route.after && (
        <button onClick={() => go({ ...route, after: undefined })}>
          جدیدترین
        </button>
      )}
    </>
  );
}
