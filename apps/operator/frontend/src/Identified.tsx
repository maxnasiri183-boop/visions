import { observePlayback, PlayButton } from "./playback";
import { useEffect, useRef, useState } from "react";
import { date, fa, Link, Route } from "./types";
import { useData } from "./hooks";

const root = "/api/operator/identified";
const portrait = (ref: string) =>
  "/api/identity/references/" + encodeURIComponent(ref) + "/face";
type Person = {
  person: string;
  display_name: string;
  source: string;
  last: number;
  appearances: number;
  reference_id: string;
};
type Visit = { id: string; source: string; first: number; last: number };
type Box = { bbox: string; width: number; height: number };
type Evidence = Box & { at: number; detection_id: string };
type Detail = {
  source: string;
  person: string;
  version: number;
  reference_id: string;
  score: number;
  evidence: Evidence | null;
};
type Span = {
  occurrence_id: string;
  media_url: string;
  media_range_s: [number, number];
  boxes: (Box & { media_pts_s: number })[];
};
type Manifest = { availability: string; spans: Span[] };

function Outline({ box }: { box: Box }) {
  const [x, y, w, h] = JSON.parse(box.bbox) as number[];
  return (
    <svg
      aria-label="چهره انتخاب‌شده"
      className="identity-outline"
      viewBox={`0 0 ${box.width} ${box.height}`}
    >
      <rect
        x={x}
        y={y}
        width={w}
        height={h}
        fill="none"
        stroke="var(--accent-hover)"
        strokeWidth={3}
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  );
}

function Clip({ span, next }: { span: Span; next: () => void }) {
  const video = useRef<HTMLVideoElement>(null),
    [time, setTime] = useState(-1);
  const [playing, setPlaying] = useState(false);
  const [error, setError] = useState(false);
  const [start, end] = span.media_range_s;
  const nextRef = useRef(next);
  nextRef.current = next;
  useEffect(() => {
    const v = video.current!;
    let finished = false;
    function update(t: number) {
      if (t >= end && !finished) {
        finished = true;
        v.pause();
        setTime(-1);
        nextRef.current();
        return;
      }
      if (!v.seeking) setTime(t);
    }
    const unsubscribe = observePlayback(v, (t, presented) => {
      if (presented || t >= end) update(t);
    });
    const limit = () => {
      if (v.currentTime >= end) update(v.currentTime);
    };
    const ended = () => update(end);
    v.addEventListener("timeupdate", limit);
    v.addEventListener("ended", ended);
    const replay = () => {
      if (finished) {
        finished = false;
        v.currentTime = start;
      }
    };
    v.addEventListener("play", replay);
    return () => {
      unsubscribe();
      v.removeEventListener("timeupdate", limit);
      v.removeEventListener("ended", ended);
      v.removeEventListener("play", replay);
    };
  }, [start, end]);
  const box = span.boxes.find(
    (b) =>
      video.current?.videoWidth === b.width &&
      video.current?.videoHeight === b.height &&
      Math.abs(b.media_pts_s - time) < 0.0001 &&
      time >= start &&
      time < end,
  );
  return (
    <>
      <div className="identity-frame">
        <video
          ref={video}
          muted
          playsInline
          onLoadedMetadata={() => {
            video.current!.currentTime = start;
            void video.current!.play().catch(() => {});
          }}
          onPlay={() => setPlaying(true)}
          onPause={() => setPlaying(false)}
          onSeeking={() => setTime(-1)}
          onError={() => setError(true)}
          src={span.media_url}
        />
        {box && <Outline box={box} />}
      </div>
      {error && <p role="alert">ویدیو در دسترس نیست.</p>}
      <PlayButton
        playing={playing}
        toggle={() => {
          const v = video.current!;
          if (v.paused) void v.play().catch(() => setError(true));
          else v.pause();
        }}
      />
      <input
        dir="ltr"
        type="range"
        aria-label="زمان ویدیو"
        min={start}
        max={end}
        step="0.01"
        value={Math.max(start, Math.min(end, time))}
        onChange={(e) => {
          setTime(-1);
          video.current!.currentTime = Number(e.target.value);
        }}
      />
    </>
  );
}

function EvidencePanel({ id, play }: { id: string; play: boolean }) {
  const url = root + "/appearances/" + encodeURIComponent(id);
  const detail = useData<Detail>(url),
    manifest = useData<Manifest>(play ? url + "/playback" : null);
  const [expanded, expand] = useState(false),
    [part, setPart] = useState(0);
  const d = detail.value,
    e = d?.evidence,
    span = manifest.value?.spans[part];
  if (!d) return <p role="status">{detail.error || "در حال دریافت شواهد…"}</p>;
  return (
    <section className="card identity-evidence">
      <h2>شواهد شناسایی · {d.person}</h2>
      <div className="identity-pair">
        <figure>
          <img src={portrait(d.reference_id)} alt="چهره مرجع ثبت‌شده" />
          <figcaption>چهره مرجع · نسخه {fa(d.version)}</figcaption>
        </figure>
        {e && (
          <figure>
            <img
              src={"/api/operator/crops/" + e.detection_id}
              alt="چهره دیده‌شده"
            />
            <figcaption>
              {d.source} · {date(e.at)}
            </figcaption>
          </figure>
        )}
      </div>
      <p>
        امتیاز شباهت{" "}
        <bdi>
          {d.score.toLocaleString("fa-IR", { maximumFractionDigits: 3 })}
        </bdi>{" "}
        · دامنه از منفی یک تا یک
      </p>
      {e ? (
        <button onClick={() => expand(!expanded)} aria-expanded={expanded}>
          نمایش فریم کامل
        </button>
      ) : (
        <p>شواهد تصویری منقضی شده است.</p>
      )}
      {expanded && e && (
        <figure>
          <div className="identity-frame">
            <img
              src={"/api/operator/original/" + e.detection_id + ".png"}
              alt="فریم کامل دوربین"
            />
            <Outline box={e} />
          </div>
          <figcaption>
            {d.source} · {date(e.at)}
          </figcaption>
        </figure>
      )}
      {play && manifest.error && (
        <p role="alert">ویدیو در دسترس نیست یا منقضی شده است.</p>
      )}
      {manifest.value?.availability === "partial" && (
        <p>بخشی از ویدیو در دسترس نیست.</p>
      )}
      {span && (
        <Clip
          key={span.occurrence_id}
          span={span}
          next={() => {
            if (part + 1 < manifest.value!.spans.length) setPart(part + 1);
          }}
        />
      )}
    </section>
  );
}

export function Identified({ route, go }: { route: Route; go: Link }) {
  const hours = Number(route.q) || 24,
    camera = route.source || "";
  const [now, setNow] = useState(() => Date.now() / 1000);

  const filter = new URLSearchParams({ hours: String(hours), camera });
  if (route.person) {
    filter.set("to", String(now));
    filter.set("from", String(now - hours * 3600));
  }
  const [page, setPage] = useState(0),
    [visitPage, setVisitPage] = useState(0);
  const people = useData<{
    items: Person[];
    cameras: string[];
    next_offset: number | null;
  }>(
    root + "/people?" + filter + "&offset=" + page,
    route.person || page ? 0 : 5000,
  );
  const visits = useData<{ items: Visit[]; next_offset: number | null }>(
    route.person
      ? root +
          "/appearances?" +
          filter +
          "&person=" +
          encodeURIComponent(route.person) +
          "&offset=" +
          visitPage
      : null,
  );
  const updates = useData<{ items: Visit[] }>(
    route.person
      ? root +
          "/appearances?hours=" +
          hours +
          "&camera=" +
          encodeURIComponent(camera) +
          "&person=" +
          encodeURIComponent(route.person)
      : null,
    5000,
  );
  const id = route.appearance || visits.value?.items[0]?.id;
  const [playing, setPlaying] = useState("");
  function filters(q: string, source: string) {
    setPage(0);
    setVisitPage(0);
    go({ view: "identified", q, source });
  }
  return (
    <>
      <h1>افراد شناسایی‌شده</h1>
      <div className="row">
        <label>
          بازه زمانی{" "}
          <select
            value={hours}
            onChange={(e) => filters(e.target.value, camera)}
          >
            {[1, 24, 168].map((h) => (
              <option key={h} value={h}>
                {fa(h)} ساعت گذشته
              </option>
            ))}
          </select>
        </label>
        <label>
          دوربین{" "}
          <select
            value={camera}
            onChange={(e) => filters(String(hours), e.target.value)}
          >
            <option value="">همه</option>
            {people.value?.cameras.map((c) => (
              <option key={c}>{c}</option>
            ))}
          </select>
        </label>
      </div>
      <div
        className={"identity-workspace " + (route.person ? "inspecting" : "")}
      >
        <section className="card identity-list">
          {people.error && <p role="alert">{people.error}</p>}
          {people.value?.items.length === 0 && (
            <p>در این بازه حضور تأییدشده‌ای ثبت نشده است.</p>
          )}
          {people.value?.items.map((p) => (
            <button
              className="record-row"
              key={p.person}
              onClick={() => {
                setPlaying("");
                setVisitPage(0);
                setNow(Date.now() / 1000);
                go({ ...route, person: p.person, appearance: undefined });
              }}
            >
              <img
                width="48"
                height="48"
                src={portrait(p.reference_id)}
                alt=""
              />
              <strong>{p.display_name || p.person}</strong>
              <span>
                {p.source} · {date(p.last)}
              </span>
              <span>{fa(p.appearances)} حضور</span>
            </button>
          ))}
          {page > 0 && <button onClick={() => setPage(page - 50)}>قبلی</button>}
          {people.value?.next_offset != null && (
            <button onClick={() => setPage(page + 50)}>بعدی</button>
          )}
        </section>
        {route.person && (
          <section>
            <button
              onClick={() =>
                go({ ...route, person: undefined, appearance: undefined })
              }
            >
              بازگشت به افراد
            </button>
            {updates.value?.items[0]?.id &&
              visits.value &&
              updates.value.items[0]?.id !== visits.value.items[0]?.id && (
                <button
                  onClick={() => {
                    setNow(Date.now() / 1000);
                    go({ ...route, appearance: id });
                  }}
                >
                  به‌روزرسانی‌های جدید
                </button>
              )}
            {id && <EvidencePanel key={id} id={id} play={playing === id} />}
            <h2>حضورها</h2>
            {visits.error && <p>{visits.error}</p>}
            {visits.value?.items.map((v) => (
              <div className="record-row" key={v.id}>
                <button
                  onClick={() => {
                    setPlaying("");
                    go({ ...route, appearance: v.id });
                  }}
                >
                  {v.source} · {date(v.first)} تا {date(v.last)}
                </button>
                <button
                  onClick={() => {
                    setPlaying(v.id);
                    go({ ...route, appearance: v.id });
                  }}
                >
                  مشاهده ویدیو
                </button>
              </div>
            ))}
            {visitPage > 0 && (
              <button onClick={() => setVisitPage(visitPage - 50)}>قبلی</button>
            )}
            {visits.value?.next_offset != null && (
              <button onClick={() => setVisitPage(visitPage + 50)}>بعدی</button>
            )}
          </section>
        )}
      </div>
    </>
  );
}
