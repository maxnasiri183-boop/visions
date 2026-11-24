import { date, fa, Link } from "./types";
import { useData } from "./hooks";
type State = { state: string; heartbeat_at: number };
type Item = {
  source_id: string;
  source?: State;
  queue?: State;
  frame: null | {
    frame_index: number;
    ingested_at: string;
    indexed_at: number;
    face_count: number;
    occurrence_id: string;
  };
  received: null | {
    frame_index: number;
    ingested_at: string;
    observed_at: number;
    lag_ms: number;
  };
  searchable_at: number | null;
  recognition_errors_5m: number | null;
  pending_clips: number;
  backlog_age_s: number;
};
type Summary = {
  at: number;
  items: Item[];
  feeder: State | null;
  worker: State | null;
  notices: { at: number; service: string; state: string }[];
  recoveries: {
    at: number;
    occurrence_id: string;
    previous_last: number;
    first_recovered: number | null;
  }[];
  expired: number;
  free_bytes: number;
};
const states: Record<string, string> = {
  source_outage: "قطعی منبع",
  retry: "تلاش مجدد برای پردازش",
  storage_guard: "توقف برای حفظ فضای دیسک",
  backpressure: "صف پردازش پر است",
  expired: "پایان زمان نگهداری",
};
export function Health({ go }: { go: Link }) {
  const data = useData<Summary>("/api/operator/health", 2000),
    s = data.value;
  return (
    <>
      <div className="page-head">
        <div>
          <h1>وضعیت سامانه</h1>
          <p>آخرین دریافت، پیشرفت پردازش و زمان آماده‌شدن جست‌وجو</p>
        </div>
        <small>{s ? date(s.at) : "در حال دریافت…"}</small>
      </div>
      {data.error && (
        <p role="alert">{data.error} آخرین اطلاعات ممکن است قدیمی باشد.</p>
      )}
      {s && (
        <>
          {!s.items.length && <p className="empty">منبعی در گردش نیست.</p>}
          <div className="health-grid">
            {s.items.map((x) => {
              const stopped =
                !s.feeder ||
                s.at - s.feeder.heartbeat_at > 10 ||
                s.feeder.state === "finished";
              const old = !x.frame || s.at - x.frame.indexed_at > 10;
              const offline = x.source?.state === "source_outage";
              const workerStopped =
                !s.worker ||
                s.at - s.worker.heartbeat_at > 10 ||
                s.worker.state === "waiting_backend";
              const guarded = s.feeder?.state === "storage_guard";
              const late =
                (x.received?.lag_ms ?? 0) > 3000 || x.backlog_age_s > 3;
              const title = offline
                ? "منبع قطع است"
                : guarded
                  ? "ورود ویدیو به‌دلیل کمبود فضا متوقف است"
                  : stopped
                    ? "خوراک‌رسان متوقف یا بی‌خبر است"
                    : workerStopped
                      ? "پردازشگر متوقف یا بی‌خبر است"
                      : old
                        ? "تصویر تازه دریافت نشده است"
                        : late
                          ? "پردازش عقب است"
                          : "منبع فعال است";
              return (
                <section className="card inside" key={x.source_id}>
                  <div className="title-row">
                    <span
                      className={
                        "dot " +
                        (!offline &&
                        !old &&
                        !guarded &&
                        !workerStopped &&
                        !stopped &&
                        !late
                          ? "green"
                          : "")
                      }
                    />
                    <h2>
                      <bdi>{x.source_id}</bdi>
                    </h2>
                  </div>
                  <h3>{title}</h3>
                  <dl>
                    <div>
                      <dt>آخرین فریم دریافت و رمزگشایی‌شده</dt>
                      <dd>
                        {x.received ? (
                          <>
                            {date(x.received.ingested_at)} · فریم{" "}
                            {fa(x.received.frame_index)}
                          </>
                        ) : (
                          "اندازه‌گیری موجود نیست"
                        )}
                      </dd>
                    </div>
                    <div>
                      <dt>آخرین پردازش</dt>
                      <dd>
                        {date(x.frame?.indexed_at ?? null)} · فریم{" "}
                        {x.frame ? fa(x.frame.frame_index) : "—"}
                      </dd>
                    </div>
                    <div>
                      <dt>آخرین مشاهدهٔ قابل جست‌وجو</dt>
                      <dd>{date(x.searchable_at)}</dd>
                    </div>
                    <div>
                      <dt>تأخیر دریافت نسبت به برنامه</dt>
                      <dd>
                        {x.received
                          ? fa(Math.max(0, x.received.lag_ms) / 1000, 2) +
                            " ثانیه"
                          : "اندازه‌گیری موجود نیست"}
                      </dd>
                    </div>
                    <div>
                      <dt>قدمت پخش‌های در انتظار</dt>
                      <dd>
                        {fa(x.backlog_age_s, 1)} ثانیه · {fa(x.pending_clips)}{" "}
                        پخش
                      </dd>
                    </div>
                    <div>
                      <dt>نتیجهٔ آخرین فریم</dt>
                      <dd>
                        {x.frame
                          ? x.frame.face_count
                            ? fa(x.frame.face_count) + " چهره"
                            : "صحنهٔ بدون چهره"
                          : "هنوز پردازش نشده"}
                      </dd>
                    </div>
                    <div>
                      <dt>خطای شناسایی در ۵ دقیقه</dt>
                      <dd>
                        {x.recognition_errors_5m == null
                          ? "نامشخص"
                          : fa(x.recognition_errors_5m)}
                      </dd>
                    </div>
                  </dl>
                  {x.frame && (
                    <button
                      onClick={() =>
                        go({
                          view: "recordings",
                          occ: x.frame!.occurrence_id,
                          frame: x.frame!.frame_index,
                        })
                      }
                    >
                      دیدن آخرین تصویر ←
                    </button>
                  )}
                </section>
              );
            })}
          </div>
          <section className="card archive">
            <h2>وقفه‌ها و بازیابی‌های اخیر</h2>
            {!s.notices.length && !s.recoveries.length && (
              <p>وقفه‌ای ثبت نشده است.</p>
            )}
            {s.notices.map((n, i) => (
              <p key={i}>
                {date(n.at)} · {states[n.state] || "تغییر وضعیت منبع"} ·{" "}
                <bdi>{n.service}</bdi>
              </p>
            ))}
            {s.recoveries.map((r, i) => (
              <p key={i}>
                بازیابی در {date(r.at)} · آخرین فریم پایدار{" "}
                {fa(r.previous_last)} · نخستین فریم پس از بازیابی{" "}
                {r.first_recovered == null
                  ? "در انتظار"
                  : fa(r.first_recovered)}
              </p>
            ))}
          </section>
          <p className="strip">
            فضای آزاد {fa(s.free_bytes / 1024 ** 3, 1)} گیبی‌بایت ·{" "}
            {fa(s.expired)} پخش منقضی‌شده
          </p>
        </>
      )}
    </>
  );
}
