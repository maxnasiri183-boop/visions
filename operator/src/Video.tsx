import { nameFa } from "./Gallery";
import React, { useEffect, useLayoutEffect, useRef, useState } from "react";
import {
  api,
  date,
  Decision,
  Face,
  fa,
  Frame,
  Link,
  pts,
  Recording,
  Route,
} from "./types";
import { useData } from "./hooks";

export function VideoPage({ route, go }: { route: Route; go: Link }) {
  const live = route.view === "live";
  const [source, setSource] = useState(""),
    [follow, setFollow] = useState(live && !route.occ);
  const list = useData<{ items: Recording[]; sources: string[] }>(
    "/api/operator/recordings?source=" + encodeURIComponent(source),
    2000,
  );
  const [chosen, setChosen] = useState<string>("");
  const newest = list.value?.items[0]?.occurrence_id;
  useEffect(() => {
    setChosen(route.occ || "");
    setFollow(live && !route.occ);
  }, [live, route.occ]);
  const id = follow ? newest : chosen || route.occ || newest;
  const detail = useData<Recording>(
    id ? "/api/operator/recordings/" + encodeURIComponent(id) : null,
    2000,
  );
  const rec =
    detail.value ?? list.value?.items.find((r) => r.occurrence_id === id);
  const keep = () => {
    if (id) {
      setChosen(id);
      setFollow(false);
    }
  };
  const freeze = (f: Frame, face?: Face) => {
    keep();
    go({
      view: route.view,
      occ: id,
      frame: f.frame_index,
      face: face?.detection_id,
    });
  };
  return (
    <>
      <div className="page-head">
        <div>
          <h1>{live ? "پخش زنده" : "ویدیوها"}</h1>
          <p>
            {live
              ? "آخرین تصاویر پردازش‌شدهٔ منابع خصوصی"
              : "بازبینی تصویر و شواهد هر مشاهده"}
          </p>
        </div>
        <label>
          منبع
          <select
            value={source}
            onChange={(e) => {
              go({ view: route.view });
              setSource(e.target.value);
              setChosen("");
              setFollow(live);
            }}
          >
            <option value="">همهٔ منابع</option>
            {list.value?.sources.map((s) => (
              <option key={s}>{s}</option>
            ))}
          </select>
        </label>
      </div>
      {list.error && <p role="alert">{list.error}</p>}
      {rec ? (
        <Player
          key={rec.occurrence_id}
          {...{ rec, route, go, follow, keep, freeze }}
          onLive={() => {
            go({ view: "live" });
            setFollow(true);
          }}
        />
      ) : (
        <section className="empty">
          {detail.error ||
            (!list.value
              ? "در حال دریافت وضعیت منبع…"
              : "هنوز تصویری برای پخش ثبت نشده است.")}
        </section>
      )}
      {!live && <Archive {...{ source, go }} />}
    </>
  );
}
function Archive({ source, go }: { source: string; go: Link }) {
  const [before, setBefore] = useState("");
  useEffect(() => setBefore(""), [source]);
  const list = useData<{ items: Recording[] }>(
    "/api/operator/recordings?source=" +
      encodeURIComponent(source) +
      (before ? "&before=" + before : ""),
    before ? 0 : 5000,
  );
  const rows = list.value?.items || [];
  return (
    <section className="card archive">
      <h2>فهرست پخش‌های ثبت‌شده</h2>
      <div className="scroll-list">
        {rows.map((r) => (
          <button
            className="record-row"
            key={r.occurrence_id}
            onClick={() =>
              go({ view: "recordings", occ: r.occurrence_id, frame: 0 })
            }
          >
            <bdi>{r.asset_id}</bdi>
            <span>
              <bdi>{r.source_id}</bdi> · {date(r.started_at || r.scheduled_at)}
            </span>
            <strong>بازبینی ←</strong>
          </button>
        ))}
      </div>
      <div className="row">
        {rows.length === 60 && (
          <button onClick={() => setBefore(String(rows.at(-1)!.scheduled_at))}>
            قدیمی‌تر
          </button>
        )}
        {before && <button onClick={() => setBefore("")}>جدیدترین</button>}
      </div>
    </section>
  );
}

type PlayerProps = {
  rec: Recording;
  route: Route;
  go: Link;
  follow: boolean;
  keep: () => void;
  onLive: () => void;
  freeze: (f: Frame, p?: Face) => void;
};
function Player({ rec, route, go, follow, keep, onLive, freeze }: PlayerProps) {
  const v = useRef<HTMLVideoElement>(null),
    stage = useRef<HTMLDivElement>(null);
  const [clock, setClock] = useState(0),
    [playing, setPlaying] = useState(false),
    [ready, setReady] = useState(false),
    [mediaError, setMediaError] = useState("");
  const [fitted, setFitted] = useState({ width: 0, height: 0 });
  const path =
    "/api/operator/recordings/" + encodeURIComponent(rec.occurrence_id);
  const target = route.occ === rec.occurrence_id ? route.frame : undefined;
  const initial = useData<{ items: Frame[]; at: number }>(
    target === undefined ? null : path + "/frames?frame=" + target,
  );
  const windowAt = Math.floor(clock / 3) * 3;
  const timeline = useData<{ items: Frame[] }>(
    path + "/frames?at=" + windowAt,
    rec.finished_at ? 0 : 900,
  );
  const [loaded, setLoaded] = useState<Frame[]>([]);
  useEffect(() => {
    if (timeline.value) setLoaded(timeline.value.items);
  }, [timeline.value]);
  const current = [...loaded]
    .reverse()
    .find((f) => f.media_pts_s <= clock + 0.00002);
  const next =
    current && loaded.find((f) => f.frame_index === current.frame_index + 1);
  const f =
    current &&
    (next
      ? clock < next.media_pts_s
      : clock <
        current.media_pts_s +
          Math.max(0.04, (rec.duration_s / rec.frame_count) * 1.3))
      ? current
      : undefined;
  const face = f?.faces.find((p) => p.detection_id === route.face);
  const seek = (s: number) => {
    if (!v.current) return;
    v.current.currentTime = Math.max(0, Math.min(rec.duration_s - 0.0001, s));
    setClock(v.current.currentTime);
  };
  useEffect(() => {
    if (initial.value && ready) {
      v.current!.pause();
      setLoaded(initial.value.items);
      seek(initial.value.at + 0.00001);
    }
  }, [ready, initial.value]);
  useEffect(() => {
    const video = v.current;
    if (!video || !ready || !follow) return;
    const edge = Math.max(
      0,
      ((rec.last_frame - 3) * rec.duration_s) / rec.frame_count,
    );
    if (Math.abs(video.currentTime - edge) > 1.2) seek(edge);
    void video.play().catch(() => setPlaying(false));
  }, [rec.last_frame, follow, ready]);
  useLayoutEffect(() => {
    const s = stage.current!,
      video = v.current!;
    const fit = () => {
      const ratio = video.videoWidth / (video.videoHeight || 1) || 4 / 3;
      const w = Math.min(s.clientWidth, s.clientHeight * ratio);
      setFitted({ width: w, height: w / ratio });
    };
    const resize = new ResizeObserver(fit);
    resize.observe(s);
    fit();
    video.addEventListener("loadedmetadata", fit);
    return () => {
      resize.disconnect();
      video.removeEventListener("loadedmetadata", fit);
    };
  }, []);
  useEffect(() => {
    const video = v.current!;
    let ticket = 0;
    function tick(_: number, metadata: VideoFrameCallbackMetadata) {
      setClock(metadata.mediaTime);
      ticket = video.requestVideoFrameCallback(tick);
    }
    if (video.requestVideoFrameCallback)
      ticket = video.requestVideoFrameCallback(tick);
    return () => {
      if (ticket) video.cancelVideoFrameCallback(ticket);
    };
  }, []);
  const choose = (p: Face) => {
    if (f) {
      v.current!.pause();
      freeze(f, p);
    }
  };
  const step = async (delta: number) => {
    keep();
    v.current?.pause();
    const index = Math.min(
      rec.last_frame,
      Math.max(0, (f?.frame_index ?? 0) + delta),
    );
    try {
      const batch = await api<{ at: number; items: Frame[] }>(
        path + "/frames?frame=" + index,
      );
      setLoaded(batch.items);
      seek(batch.at + 0.00001);
    } catch {
      setMediaError("این فریم در دسترس نیست.");
    }
  };
  const age = Math.max(
    0,
    Date.now() / 1000 - Date.parse(f?.ingested_at || "") / 1000,
  );
  return (
    <div className="review-grid">
      <section className="card player">
        <header className="bar">
          <div className="title-row">
            <span className={"dot " + (follow ? "green" : "")} />
            <h2>
              <bdi>{rec.source_id}</bdi>
            </h2>
          </div>
          <span>
            {rec.cycle > 0 ? "بازپخش محتوای تکراری" : "پخش ثبت‌شده"}
            {rec.synthetic ? " · ترکیبی / آزمایشی" : ""}
          </span>
        </header>
        <div className="stage" ref={stage}>
          <video
            ref={v}
            muted
            playsInline
            preload="auto"
            src={path + "/media"}
            onLoadedMetadata={() => setReady(true)}
            onSeeked={() => setClock(v.current?.currentTime || 0)}
            onTimeUpdate={() => {
              if (
                v.current &&
                typeof v.current.requestVideoFrameCallback !== "function"
              )
                setClock(v.current.currentTime);
            }}
            onPlay={() => setPlaying(true)}
            onPause={() => setPlaying(false)}
            onError={() =>
              setMediaError(
                "ویدیو قابل پخش نیست یا مدت نگهداری آن به پایان رسیده است.",
              )
            }
          />
          <div className="overlay" style={fitted}>
            {f?.faces.map((p, i) => (
              <button
                key={p.detection_id}
                className={"box " + (p === face ? "active" : "")}
                style={{
                  left: (p.box[0] / f.width) * 100 + "%",
                  top: (p.box[1] / f.height) * 100 + "%",
                  width: (p.box[2] / f.width) * 100 + "%",
                  height: (p.box[3] / f.height) * 100 + "%",
                }}
                aria-label={"انتخاب چهرهٔ " + fa(i + 1)}
                onClick={() => choose(p)}
              >
                <span>{fa(i + 1)}</span>
              </button>
            ))}
          </div>
          {mediaError && (
            <div role="alert" className="video-error">
              {mediaError}
            </div>
          )}
        </div>
        <div className="transport">
          <button
            onClick={() => {
              keep();
              if (playing) v.current?.pause();
              else
                void v.current
                  ?.play()
                  .catch(() => setMediaError("پخش ویدیو انجام نشد."));
            }}
          >
            {playing ? "توقف" : "پخش"}
          </button>
          <input
            type="range"
            dir="ltr"
            min="0"
            max={rec.duration_s}
            step=".001"
            value={clock}
            aria-label="زمان پخش"
            onChange={(e) => {
              keep();
              v.current?.pause();
              seek(Number(e.target.value));
            }}
          />
          <bdi>
            {pts(clock)} / {pts(rec.duration_s)}
          </bdi>
        </div>
        <div className="row controls">
          <button onClick={() => void step(-1)}>فریم قبلی</button>
          <button onClick={() => void step(1)}>فریم بعدی</button>
          <button
            onClick={() => {
              if (document.fullscreenElement) void document.exitFullscreen();
              else void stage.current?.requestFullscreen();
            }}
          >
            تمام‌صفحه
          </button>
          {!follow && route.view === "live" && (
            <button onClick={onLive}>رفتن به پخش زنده</button>
          )}
        </div>
        <div className="strip">
          <span>فریم {f ? fa(f.frame_index) : "—"}</span>
          <span>{f ? date(f.ingested_at) : "زمان ثبت نامشخص"}</span>
          {follow && Number.isFinite(age) && (
            <span>تصویر {fa(age)} ثانیه پیش</span>
          )}
        </div>
      </section>
      <aside className="card inspector">
        <div className="bar">
          <h2>بررسی مشاهده</h2>
          {f && <span>{fa(f.faces.length)} چهره</span>}
        </div>
        {f?.faces.length ? (
          <>
            <div className="face-list">
              {f.faces.map((p, i) => (
                <button
                  key={p.detection_id}
                  aria-pressed={p === face}
                  onClick={() => choose(p)}
                >
                  چهرهٔ {fa(i + 1)}
                </button>
              ))}
            </div>
            {face ? (
              <Inspect key={face.detection_id} {...{ face, f, go }} />
            ) : (
              <p className="empty">
                برای دیدن نتیجه، یکی از چهره‌های تصویر را انتخاب کنید.
              </p>
            )}
          </>
        ) : (
          <p className="empty">
            {!f
              ? rec.state === "failed"
                ? "خطا در پردازش فریم"
                : "در انتظار نتیجهٔ این فریم"
              : "این فریم پردازش شد؛ چهره‌ای در تصویر نبود."}
          </p>
        )}
      </aside>
    </div>
  );
}
const titles: Record<string, string> = {
  pending: "در انتظار شناسایی",
  poor_quality: "کیفیت تصویر کافی نیست",
  error: "خطای فنی در شناسایی",
  unknown: "ناشناخته",
  matched: "هویت پیشنهادی",
};
function Inspect({ face, f, go }: { face: Face; f: Frame; go: Link }) {
  const [technical, setTechnical] = useState(false),
    [done, setDone] = useState(false);
  const d = useData<Decision>(
    "/api/identity/observations/" + face.detection_id,
    done ? 0 : 1200,
  );
  useEffect(() => {
    if (d.value && d.value.state !== "pending") setDone(true);
  }, [d.value]);
  const result = d.value;
  const [copied, setCopied] = useState(false);
  const details = useData<{ event: object; sql: object | null }>(
    technical ? "/api/operator/details/" + face.detection_id : null,
  );
  return (
    <div className="inside">
      <h3>
        {d.error
          ? "دریافت نتیجه ممکن نیست"
          : titles[result?.state || "pending"]}
      </h3>
      {result?.state === "matched" && <h2>{nameFa(result.display_name)}</h2>}
      <div className="pair">
        <figure>
          <img
            src={"/api/operator/crops/" + face.detection_id}
            alt="برش واقعی چهره"
          />
          <figcaption>چهرهٔ مشاهده‌شده</figcaption>
        </figure>
        <figure>
          {result?.portrait_url ? (
            <img
              src={result.face_url || result.portrait_url}
              alt="نزدیک‌ترین تصویر مرجع واجد شرایط"
            />
          ) : (
            <div className="portrait-empty">تصویر مرجع موجود نیست</div>
          )}
          <figcaption>نزدیک‌ترین تصویر مرجع</figcaption>
        </figure>
      </div>
      <dl>
        <div>
          <dt>زمان مشاهده</dt>
          <dd>{date(f.ingested_at)}</dd>
        </div>
        <div>
          <dt>امتیاز شباهت</dt>
          <dd>{result?.score != null ? fa(result.score, 3) : "—"}</dd>
        </div>
        <div>
          <dt>آستانهٔ تصمیم</dt>
          <dd>{result?.threshold != null ? fa(result.threshold, 3) : "—"}</dd>
        </div>
      </dl>
      {(result?.person_id || result?.candidate_person_id) && (
        <button
          onClick={() =>
            go({
              view: "gallery",
              person: result.person_id || result.candidate_person_id,
              version: String(result.version),
            })
          }
        >
          {result.person_id ? "پروندهٔ مرجع" : "پروندهٔ نزدیک‌ترین مرجع"} ←
        </button>
      )}
      <details onToggle={(e) => setTechnical(e.currentTarget.open)}>
        <summary>جزئیات فنی</summary>
        <p>شناسهٔ مشاهده</p>
        <code>{face.detection_id}</code>
        <div className="row">
          <button
            onClick={() => {
              void navigator.clipboard
                .writeText(face.detection_id)
                .then(() => setCopied(true))
                .catch(() => setCopied(false));
            }}
          >
            {copied ? "کپی شد" : "کپی شناسه"}
          </button>
          <a
            href={"/api/operator/original/" + face.detection_id + ".png"}
            target="_blank"
            rel="noreferrer"
          >
            باز کردن فریم اصلی
          </a>
        </div>
        <p>
          زمان تا مشاهده در پایگاه داده{" "}
          {face.sql_visible_at == null
            ? "در انتظار"
            : fa(
                Math.max(
                  0,
                  1000 * face.sql_visible_at - Date.parse(f.ingested_at),
                ),
              ) + " میلی‌ثانیه"}
        </p>
        <p>
          موضوع کافکا <bdi>{face.topic}</bdi> · آخرین موقعیت{" "}
          <bdi>{face.latest_offset}</bdi>
        </p>
        <p>
          نسخهٔ گالری <bdi>{result?.version || "—"}</bdi>
        </p>
        <p>اطمینان تشخیص چهره {fa(face.confidence, 4)}</p>
        <p>
          موقعیت کافکا{" "}
          <bdi>
            {face.partition_id} / {face.kafka_offset}
          </bdi>
        </p>
        {technical && (
          <pre dir="ltr">{JSON.stringify(details.value ?? {}, null, 2)}</pre>
        )}
      </details>
    </div>
  );
}
