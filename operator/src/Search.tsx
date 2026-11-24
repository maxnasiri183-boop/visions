import { useRef, useState } from "react";
import { api, date, fa, Link, post, pts } from "./types";
import { useData } from "./hooks";
type Probe = {
  probe_id: string;
  image_url: string;
  faces: {
    face_index: number;
    bbox_xywh: number[];
    confidence: number;
    thumbnail_url: string;
    reason?: string;
  }[];
};
type Hit = {
  occurrence_id: string;
  frame_index: number;
  detection_id: string;
  media_pts_s: number;
  score: number;
  thumbnail_url: string;
  observed_utc?: string;
};
type Group = {
  asset_id?: string;
  repeated?: boolean;
  occurrence_id: string;
  source_id: string;
  first_s: number;
  last_s: number;
  score: number;
  thumbnail_url: string;
  observations: Hit[];
};
type Results = {
  groups: Group[];
  candidate_count: number;
  match_threshold: number;
};
export function Search({ go }: { go: Link }) {
  const [probe, setProbe] = useState<Probe>(),
    [face, setFace] = useState<number>(),
    [results, setResults] = useState<Results>(),
    [working, setWorking] = useState(false),
    [error, setError] = useState("");
  const [source, setSource] = useState(""),
    [from, setFrom] = useState(""),
    [to, setTo] = useState("");
  const [sort, setSort] = useState("rank");
  const input = useRef<HTMLInputElement>(null);
  const sources = useData<{ sources: string[] }>("/api/operator/recordings");
  const examples = useData<{ label: string; url: string }[]>(
    "/api/operator/examples",
  );
  async function upload(file: Blob) {
    setWorking(true);
    setError("");
    setResults(undefined);
    setProbe(undefined);
    setFace(undefined);
    try {
      if (file.size > 8 * 1024 ** 2)
        throw Error("حجم تصویر باید کمتر از ۸ مگابایت باشد.");
      const form = new FormData();
      form.append("file", file, "probe.jpg");
      const data = await api<Probe>("/api/identity/probes", {
        method: "POST",
        body: form,
      });
      setProbe(data);
      if (data.faces.length === 1 && !data.faces[0].reason)
        setFace(data.faces[0].face_index);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setWorking(false);
    }
  }
  async function search() {
    if (!probe || face === undefined) return;
    setError("");
    setWorking(true);
    setResults(undefined);
    try {
      if (from && to && from > to)
        throw Error("زمان پایان باید بعد از زمان آغاز باشد.");
      setResults(
        await post<Results>("/api/identity/search", {
          probe_id: probe.probe_id,
          face_index: face,
          source_id: source || undefined,
          from_utc: from ? new Date(from).toISOString() : undefined,
          to_utc: to ? new Date(to).toISOString() : undefined,
          limit: 25,
        }),
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setWorking(false);
    }
  }
  function open(hit: Hit) {
    go({
      view: "recordings",
      occ: hit.occurrence_id,
      frame: hit.frame_index,
      face: hit.detection_id,
    });
  }
  const groups = [...(results?.groups || [])];
  if (sort === "time")
    groups.sort((a, b) =>
      String(b.observations[0]?.observed_utc || "").localeCompare(
        String(a.observations[0]?.observed_utc || ""),
        undefined,
        { numeric: true },
      ),
    );
  return (
    <>
      <div className="page-head">
        <div>
          <h1>جست‌وجو با تصویر</h1>
          <p>یک چهره را در ویدیوهای ثبت‌شده پیدا کنید.</p>
        </div>
      </div>
      <div className="search-layout">
        <section className="card inside">
          <h2>تصویر جست‌وجو</h2>
          <input
            className="sr"
            type="file"
            ref={input}
            accept="image/jpeg,image/png"
            aria-label="بارگذاری عکس"
            onChange={(e) => {
              if (e.target.files?.[0]) void upload(e.target.files[0]);
              e.target.value = "";
            }}
          />
          <button
            disabled={working}
            className="drop"
            onClick={() => input.current?.click()}
            onDragOver={(e) => e.preventDefault()}
            onDrop={(e) => {
              e.preventDefault();
              if (!working && e.dataTransfer.files[0])
                void upload(e.dataTransfer.files[0]);
            }}
          >
            {probe ? (
              <img src={probe.image_url} alt="عکس بارگذاری‌شده" />
            ) : (
              <span className="upload-icon" aria-hidden="true">
                ↑
              </span>
            )}
            <strong>
              {probe ? "تغییر تصویر" : "انتخاب یا رها کردن تصویر"}
            </strong>
            <small>JPEG یا PNG · تا ۸ مگابایت</small>
          </button>
          {examples.value?.length ? (
            <details>
              <summary>آزمایش با عکس نمونه</summary>
              <div className="row">
                {examples.value.map((ex) => (
                  <button
                    disabled={working}
                    key={ex.url}
                    onClick={async () => {
                      try {
                        const r = await fetch(ex.url);
                        if (!r.ok) throw Error();
                        await upload(await r.blob());
                      } catch {
                        setError("تصویر نمونه در دسترس نیست.");
                      }
                    }}
                  >
                    {ex.label}
                  </button>
                ))}
              </div>
            </details>
          ) : null}
          {probe && (
            <>
              <h3>انتخاب چهره</h3>
              {probe.faces.length ? (
                <div className="picker">
                  {probe.faces.map((p, i) => (
                    <button
                      key={p.face_index}
                      disabled={!!p.reason}
                      aria-pressed={face === p.face_index}
                      onClick={() => {
                        setFace(p.face_index);
                        setResults(undefined);
                      }}
                    >
                      <img src={p.thumbnail_url} alt="" />
                      <span>
                        چهرهٔ {fa(i + 1)}
                        {p.reason ? " · کیفیت ناکافی" : ""}
                      </span>
                    </button>
                  ))}
                </div>
              ) : (
                <p>چهرهٔ قابل استفاده‌ای در عکس پیدا نشد.</p>
              )}
            </>
          )}
          <label>
            منبع
            <select
              value={source}
              onChange={(e) => {
                setSource(e.target.value);
                setResults(undefined);
              }}
            >
              <option value="">همهٔ منابع</option>
              {sources.value?.sources.map((x) => (
                <option key={x}>{x}</option>
              ))}
            </select>
          </label>
          <div className="dates">
            <label>
              از زمان محلی
              <input
                type="datetime-local"
                value={from}
                onChange={(e) => {
                  setFrom(e.target.value);
                  setResults(undefined);
                }}
              />
            </label>
            <label>
              تا زمان محلی
              <input
                type="datetime-local"
                value={to}
                onChange={(e) => {
                  setTo(e.target.value);
                  setResults(undefined);
                }}
              />
            </label>
          </div>
          <button
            className="primary"
            disabled={face === undefined || working}
            onClick={() => void search()}
          >
            جست‌وجوی حضورها
          </button>
          {working && <p role="status">در حال پردازش…</p>}
          {error && <p role="alert">{error}</p>}
        </section>
        <section className="card">
          <div className="bar">
            <h2>نتایج جست‌وجو</h2>
            {results && (
              <select
                aria-label="مرتب‌سازی نتایج"
                value={sort}
                onChange={(e) => setSort(e.target.value)}
              >
                <option value="rank">بیشترین شباهت</option>
                <option value="time">جدیدترین زمان</option>
              </select>
            )}
          </div>
          {results ? (
            <div className="inside">
              <p>
                {fa(groups.length)} حضور · آستانهٔ شباهت{" "}
                {fa(results.match_threshold, 3)}
              </p>
              {!groups.length && (
                <p className="empty">
                  در این بازه حضوری با شباهت کافی یافت نشد.
                </p>
              )}
              {groups.map((g, i) => {
                const best = g.observations.reduce((a, b) =>
                  a.score >= b.score ? a : b,
                );
                return (
                  <article className="hit" key={best.detection_id}>
                    <img src={g.thumbnail_url} alt="چهره در ویدیو" />
                    <div>
                      <h3>
                        حضور {fa(i + 1)} · <bdi>{g.source_id}</bdi>
                        {g.asset_id && (
                          <>
                            {" "}
                            / <bdi>{g.asset_id}</bdi>
                          </>
                        )}
                      </h3>
                      <small>
                        {date(best.observed_utc ?? null)} · {pts(g.first_s)} تا{" "}
                        {pts(g.last_s)}
                        {g.repeated ? " · بازپخش محتوای تکراری" : ""}
                      </small>
                      <p>امتیاز شباهت {fa(g.score, 3)}</p>
                      <button onClick={() => open(best)}>
                        باز کردن لحظهٔ ویدیو ←
                      </button>
                      <details>
                        <summary>{fa(g.observations.length)} مشاهده</summary>
                        {g.observations.map((o) => (
                          <button key={o.detection_id} onClick={() => open(o)}>
                            فریم {fa(o.frame_index)} · {pts(o.media_pts_s)}
                          </button>
                        ))}
                      </details>
                    </div>
                  </article>
                );
              })}
            </div>
          ) : (
            <p className="empty">
              پس از انتخاب چهره، حضورهای مشابه اینجا نمایش داده می‌شوند.
            </p>
          )}
        </section>
      </div>
    </>
  );
}
