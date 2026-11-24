import { FeedSpeed } from "./FeedSpeed";
import { FaceOverlay } from "./FaceOverlay";
import {
  ArrowRight,
  Camera,
  ChevronDown,
  Clapperboard,
  Film,
  ListVideo,
  Maximize,
  Minimize,
  Pause,
  Play,
  ScanFace,
  SkipBack,
  SkipForward,
} from "lucide-react";
import { Evidence, Inspector } from "./Inspector";
import { AppearanceTrack } from "./AppearanceTrack";
import { Timeline } from "./Timeline";
import { useMedia } from "./hooks";
import React, { useEffect, useLayoutEffect, useRef, useState } from "react";
import { date, Face, fa, Frame, Link, pts, Recording, Route } from "./types";
import { useData } from "./hooks";

export function VideoPage({ route, go }: { route: Route; go: Link }) {
  const live = route.view === "live";
  const source = route.source || "";
  const [follow, setFollow] = useState(live && !route.occ);
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
      ...route,
      view: route.view,
      occ: id,
      frame: f.frame_index,
      face: face?.detection_id,
    });
  };
  return (
    <>
      <div className="page-head">
        <div className="page-title">
          <span className="heading-icon">
            {live ? <Camera /> : <Clapperboard />}
          </span>
          <div>
            <h1>{live ? "پخش زنده" : "بازبینی ویدئو"}</h1>
            {rec && (
              <p>
                <bdi>{rec.asset_id}</bdi>
                <span className="separator">/</span>
                <bdi>{rec.source_id}</bdi>
              </p>
            )}
          </div>
        </div>
        <div className="row">
          {route.originOcc && (
            <button
              className="return-moment"
              onClick={() =>
                go({
                  view: route.originView || "recordings",
                  occ: route.originOcc,
                  frame: route.originFrame,
                  face: route.originFace,
                })
              }
            >
              <ArrowRight />
              بازگشت به لحظه قبلی
            </button>
          )}
          {live && source && <FeedSpeed key={source} source={source} />}
          <label className="inline-filter">
            <Camera />
            <span className="sr">منبع</span>
            <select
              value={source}
              onChange={(e) => {
                go({ view: route.view, source:e.target.value || undefined });
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
      </div>
      {list.error && <p role="alert">{list.error}</p>}
      {rec?.state === "expired" ? (
        <p className="empty" role="status">مدت نگهداری این ضبط به پایان رسیده است.</p>
      ) : rec ? (
        <Player
          key={rec.occurrence_id}
          {...{ rec, route, go, follow, keep, freeze }}
          onLive={() => {
            go({ view: "live", source: route.source });
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
    <details className="card archive record-archive">
      <summary>
        <ListVideo />
        بایگانی ویدئوها
        <ChevronDown />
      </summary>
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
    </details>
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
    path + (follow ? "/frames?live=1" : "/frames?at=" + windowAt),
    rec.finished_at ? 0 : follow ? 400 : 900,
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
  const liveLimit = useRef<number>();
  useLayoutEffect(() => {
    liveLimit.current = follow ? loaded.at(-1)?.media_pts_s : undefined;
  }, [follow, loaded]);
  useEffect(() => {
    const video = v.current;
    const end = liveLimit.current;
    if (!video || !ready || !follow || end === undefined) return;
    const first = loaded[0].media_pts_s;
    const buffered = Math.max(first, end - 1);
    if (video.currentTime < first || video.currentTime > end + 0.002 ||
        video.currentTime < buffered - 1) seek(buffered);
    if (end - video.currentTime >= 0.2)
      void video.play().catch(() => setPlaying(false));
  }, [loaded, follow, ready]);
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
    function display(pts: number) {
      const limit = liveLimit.current;
      if (limit !== undefined && pts >= limit && !video.paused) {
        video.pause();
        if (Math.abs(video.currentTime - limit) > 0.002)
          video.currentTime = limit + 0.00001;
        setClock(limit);
      } else {
        setClock(pts);
      }
    }
    function tick(_: number, metadata: VideoFrameCallbackMetadata) {
      display(metadata.mediaTime);
      ticket = video.requestVideoFrameCallback(tick);
    }
    const updateTime = () => display(video.currentTime);
    if (video.requestVideoFrameCallback)
      ticket = video.requestVideoFrameCallback(tick);
    else video.addEventListener("timeupdate", updateTime);
    return () => {
      if (ticket) video.cancelVideoFrameCallback(ticket);
      video.removeEventListener("timeupdate", updateTime);
    };
  }, []);
  const mobile = useMedia("(max-width: 760px)");
  const [dismissed, setDismissed] = useState<string>(),
    [appearance, setAppearance] = useState<string>(),
    [speed, setSpeed] = useState(1),
    [full, setFull] = useState(false);
  const appearanceSection = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const changed = () => setFull(Boolean(document.fullscreenElement));
    document.addEventListener("fullscreenchange", changed);
    return () => document.removeEventListener("fullscreenchange", changed);
  }, []);
  useEffect(() => {
    if (appearance && face?.detection_id === appearance && mobile)
      appearanceSection.current?.scrollIntoView({ block: "nearest" });
  }, [appearance, mobile, face?.detection_id]);
  const choose = (p: Face) => {
    if (f) {
      v.current!.pause();
      setDismissed(undefined);
      setAppearance(undefined);
      freeze(f, p);
    }
  };
  const seekHere = (pos: number) => {
    keep();
    v.current?.pause();
    seek(pos);
    setAppearance(undefined);
  };
  const step = (delta: number) => {
    keep();
    v.current?.pause();
    const index = Math.min(
      rec.last_frame,
      Math.max(0, (f?.frame_index ?? 0) + delta),
    );
    go({ ...route, occ: rec.occurrence_id, frame: index, face: undefined });
  };
  const picker =
    f && f.faces.length > 0 ? (
      <div className="face-list" aria-label="چهره‌های این فریم">
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
    ) : null;
  const message = !f
    ? rec.state === "failed" || rec.state === "retry"
      ? "خطا در پردازش فریم"
      : "در انتظار نتیجهٔ این فریم"
    : !f.faces.length
      ? "این فریم پردازش شد؛ چهره‌ای در تصویر نبود."
      : "برای دیدن نتیجه، یکی از چهره‌های تصویر را انتخاب کنید.";
  const trackOpen = Boolean(face && face.detection_id === appearance);
  const mobileClosed = mobile && face && dismissed === face.detection_id;
  const age = f
    ? Math.max(0, Date.now() / 1000 - Date.parse(f.ingested_at) / 1000)
    : null;
  return (
    <div className="review-grid">
      <section className="video-column" aria-label="پخش و خط زمان">
        <div className="player card">
          <div className="stage" ref={stage} dir="ltr">
            <video
              ref={v}
              muted
              playsInline
              preload="auto"
              src={path + "/media"}
              onLoadedMetadata={() => setReady(true)}
              onSeeked={() => setClock(v.current?.currentTime || 0)}
              onPlay={() => setPlaying(true)}
              onPause={() => setPlaying(false)}
              onError={() =>
                setMediaError(
                  "ویدیو قابل پخش نیست یا مدت نگهداری آن به پایان رسیده است.",
                )
              }
            />
            <div className="video-badges" dir="rtl">
              <span>
                <Camera />
                <bdi>{rec.source_id}</bdi>
              </span>
              <span>
                <i className={follow ? "dot green" : "dot"} />
                {follow
                  ? age !== null && age > 10
                    ? "تصویر قدیمی"
                    : "پخش زنده"
                  : "ضبط‌شده"}
              </span>
            </div>
            <FaceOverlay
              frame={f}
              selected={face?.detection_id}
              fitted={fitted}
              onChoose={choose}
            />
            {mediaError && (
              <div className="video-error" role="alert">
                {mediaError}
              </div>
            )}
            <div className="player-controls">
              <input
                type="range"
                dir="ltr"
                aria-label="زمان پخش"
                min="0"
                max={rec.duration_s}
                step=".001"
                value={clock}
                style={
                  {
                    "--progress":
                      Math.min(100, (100 * clock) / rec.duration_s) + "%",
                  } as React.CSSProperties
                }
                onChange={(e) => seekHere(+e.target.value)}
              />
              <div className="transport" dir="ltr">
                <button
                  className="icon-only"
                  aria-label={playing ? "توقف" : "پخش"}
                  title={playing ? "توقف" : "پخش"}
                  onClick={() => {
                    keep();
                    if (playing) v.current?.pause();
                    else
                      void v.current
                        ?.play()
                        .catch(() => setMediaError("پخش انجام نشد."));
                  }}
                >
                  {playing ? <Pause /> : <Play />}
                </button>
                <button
                  className="icon-only"
                  aria-label="فریم قبلی"
                  title="فریم قبلی"
                  onClick={() => step(-1)}
                >
                  <SkipBack />
                </button>
                <button
                  className="icon-only"
                  aria-label="فریم بعدی"
                  title="فریم بعدی"
                  onClick={() => step(1)}
                >
                  <SkipForward />
                </button>
                <bdi className="clock">
                  {pts(clock)} <span>/ {pts(rec.duration_s)}</span>
                </bdi>
                <select
                  className="speed"
                  value={speed}
                  aria-label="سرعت پخش"
                  title="سرعت پخش"
                  onChange={(e) => {
                    keep();
                    setSpeed(+e.target.value);
                    if (v.current) v.current.playbackRate = +e.target.value;
                  }}
                >
                  {[0.5, 1, 1.5, 2].map((n) => (
                    <option value={n} key={n}>
                      {n}×
                    </option>
                  ))}
                </select>
                <button
                  className="icon-only"
                  aria-label="تمام‌صفحه"
                  title="تمام‌صفحه"
                  onClick={() => {
                    const req = document.fullscreenElement
                      ? document.exitFullscreen()
                      : stage.current?.requestFullscreen();
                    void req?.catch(() =>
                      setMediaError("تمام‌صفحه در این مرورگر در دسترس نیست."),
                    );
                  }}
                >
                  {full ? <Minimize /> : <Maximize />}
                </button>
              </div>
            </div>
          </div>
        </div>
        <Timeline
          mediaUrl={path + "/media"}
          duration={rec.duration_s}
          clock={clock}
          frames={loaded}
          onSeek={seekHere}
        />
        {trackOpen && face && f && (
          <div ref={appearanceSection}>
            <AppearanceTrack
              key={face.detection_id}
              id={face.detection_id}
              occ={rec.occurrence_id}
              assetId={rec.asset_id}
              onOpen={(hit) =>
                go({
                  view: "recordings",
                  occ: hit.occurrence_id,
                  frame: hit.frame_index,
                  face: hit.detection_id,
                  originView:
                    route.originView ||
                    (route.view === "live" ? "live" : "recordings"),
                  originOcc: route.originOcc || rec.occurrence_id,
                  originFrame: route.originFrame ?? f.frame_index,
                  originFace: route.originFace || face.detection_id,
                })
              }
            />
          </div>
        )}
        <div className="source-strip">
          <span>
            <Film />
            <bdi>{rec.asset_id}</bdi>
          </span>
          <span>
            فریم <bdi>{f ? fa(f.frame_index) : "—"}</bdi>
          </span>
          {f && <bdi dir="ltr">{date(f.ingested_at)}</bdi>}
          {rec.synthetic === 1 && <span>ترکیبی / آزمایشی</span>}
          {rec.cycle > 0 && <span>بازپخش</span>}
          {follow && age !== null && <span>{fa(age)} ثانیه پیش</span>}
          {!follow && route.view === "live" && (
            <button onClick={onLive}>رفتن به پخش زنده</button>
          )}
        </div>
      </section>
      <Inspector
        mobile={mobile}
        selected={mobileClosed ? undefined : face?.detection_id}
        count={f?.faces.length || 0}
        onDismiss={() => {
          setDismissed(face?.detection_id);
          requestAnimationFrame(() =>
            stage.current
              ?.querySelector<HTMLButtonElement>(".box.active")
              ?.focus({ preventScroll: true }),
          );
        }}
      >
        {(!mobile || (!mobileClosed && face)) && picker}
        {mobileClosed ? (
          <button onClick={() => setDismissed(undefined)}>شناسایی چهره</button>
        ) : face && f ? (
          <Evidence
            key={face.detection_id}
            face={face}
            frame={f}
            rec={rec}
            go={go}
            appearancesOpen={trackOpen}
            onAppearances={() => {
              setAppearance(trackOpen ? undefined : face.detection_id);
              if (mobile) setDismissed(face.detection_id);
            }}
          />
        ) : (
          <div className="inspector-empty">
            {!mobile && <ScanFace />}
            <p>{message}</p>
          </div>
        )}
      </Inspector>
    </div>
  );
}
