import { recordingMoment } from "./navigation";
import { useData } from "./hooks";
import { fa, Link, pts } from "./types";
type Sighting = {
  occurrence_id: string;
  asset_id: string;
  source_id: string;
  frame_index: number;
  media_pts_s: number;
  detection_id: string;
  version: number;
};
export function ReferenceAppearances({
  reference,
  version,
  go,
}: {
  reference: string;
  version?: string;
  go: Link;
}) {
  const data = useData<{ items: Sighting[] }>(
    "/api/identity/references/" +
      encodeURIComponent(reference) +
      "/appearances" +
      (version ? "?version=" + encodeURIComponent(version) : ""),
  );
  return (
    <section aria-label="مشاهده‌های ویدئویی">
      <h4>مشاهده‌های ویدئویی</h4>
      {data.error ? (
        <p role="alert">{data.error}</p>
      ) : !data.value ? (
        <p>در حال دریافت…</p>
      ) : !data.value.items.length ? (
        <p>در ضبط‌های بررسی‌شده نتیجهٔ ثبت‌شده‌ای پیدا نشد.</p>
      ) : (
        data.value.items.map((s) => (
          <button
            className="record-row"
            key={s.detection_id}
            onClick={() => go(recordingMoment(s, s.source_id))}
          >
            <span>
              باز کردن ویدئو · <bdi>{s.asset_id}</bdi>
            </span>
            <small>
              <bdi>{s.source_id}</bdi> ·{" "}
              <bdi dir="ltr">{pts(s.media_pts_s)}</bdi> · تصمیم ثبت‌شدهٔ نسخهٔ{" "}
              {fa(s.version)}
            </small>
          </button>
        ))
      )}
    </section>
  );
}
