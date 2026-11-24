import { memo, useEffect, useMemo, useRef, useState } from "react";
import { fa, Frame, pts } from "./types";
import "./timeline.css";

type Props = {
  mediaUrl: string;
  duration: number;
  clock: number;
  frames: Frame[];
  onSeek: (seconds: number) => void;
};

const SAMPLE_COUNT = 12;
const strips = new Map<string, string[]>();

function waitForMedia(
  video: HTMLVideoElement,
  event: string,
  signal: AbortSignal,
  change: () => void,
) {
  return new Promise<void>((resolve, reject) => {
    signal.throwIfAborted();
    function finish(error?: unknown) {
      video.removeEventListener(event, ready);
      video.removeEventListener("error", fail);
      signal.removeEventListener("abort", fail);
      if (error) reject(error);
      else resolve();
    }
    const ready = () => finish();
    const fail = () =>
      finish(signal.reason || video.error || new Error("Preview unavailable"));
    video.addEventListener(event, ready, { once: true });
    video.addEventListener("error", fail, { once: true });
    signal.addEventListener("abort", fail, { once: true });
    try {
      change();
    } catch (error) {
      finish(error);
    }
  });
}

async function captureStrip(address: URL, signal: AbortSignal) {
  const video = document.createElement("video");
  const canvas = document.createElement("canvas");
  const context = canvas.getContext("2d", { alpha: false });
  if (!context) throw new Error("Canvas unavailable");
  video.muted = true;
  video.playsInline = true;
  video.preload = "metadata";
  try {
    await waitForMedia(video, "loadedmetadata", signal, () => {
      video.src = address.href;
    });
    if (
      !Number.isFinite(video.duration) ||
      video.duration <= 0 ||
      !video.videoWidth ||
      !video.videoHeight
    ) {
      throw new Error("Media dimensions unavailable");
    }
    const scale = Math.min(160 / video.videoWidth, 96 / video.videoHeight, 1);
    canvas.width = Math.max(1, Math.round(video.videoWidth * scale));
    canvas.height = Math.max(1, Math.round(video.videoHeight * scale));
    const images: string[] = [];
    for (let i = 0; i < SAMPLE_COUNT; i++) {
      await waitForMedia(video, "seeked", signal, () => {
        video.currentTime = ((i + 0.5) / SAMPLE_COUNT) * video.duration;
      });
      context.drawImage(video, 0, 0, canvas.width, canvas.height);
      images.push(canvas.toDataURL("image/jpeg", 0.72));
    }
    return images;
  } finally {
    video.removeAttribute("src");
    video.load();
  }
}

const Filmstrip = memo(function Filmstrip({ mediaUrl }: { mediaUrl: string }) {
  const [result, setResult] = useState<{
    url: string;
    images?: string[];
    failed?: boolean;
  }>();
  useEffect(() => {
    if (!mediaUrl) return;
    let address: URL;
    try {
      address = new URL(mediaUrl, window.location.href);
      if (
        !["https:", "http:"].includes(address.protocol) ||
        address.origin !== location.origin
      )
        throw Error();
    } catch {
      setResult({ url: mediaUrl, failed: true });
      return;
    }
    const cached = strips.get(address.href);
    if (cached) {
      strips.delete(address.href);
      strips.set(address.href, cached);
      setResult({ url: mediaUrl, images: cached });
      return;
    }
    const controller = new AbortController();
    let mounted = true;
    let deadline: number;
    // Rapid switches cancel during this delay, before creating another decoder.
    const start = window.setTimeout(async () => {
      deadline = window.setTimeout(() => controller.abort(), 8000);
      try {
        const images = await captureStrip(address, controller.signal);
        if (!mounted) return;
        strips.set(address.href, images);
        if (strips.size > 6) strips.delete(strips.keys().next().value!);
        setResult({ url: mediaUrl, images });
      } catch {
        if (mounted) setResult({ url: mediaUrl, failed: true });
      } finally {
        window.clearTimeout(deadline);
      }
    }, 120);
    return () => {
      mounted = false;
      window.clearTimeout(start);
      window.clearTimeout(deadline);
      controller.abort();
    };
  }, [mediaUrl]);
  const current = result?.url === mediaUrl ? result : undefined;
  return (
    <div className="vt-film">
      {current?.images ? (
        current.images.map((src, i) => (
          <img key={i} src={src} alt="" draggable={false} />
        ))
      ) : (
        <span className="vt-fallback" dir="rtl">
          {!mediaUrl || current?.failed
            ? "پیش‌نمایش در دسترس نیست"
            : "در حال دریافت پیش‌نمایش…"}
        </span>
      )}
    </div>
  );
});

const Ruler = memo(function Ruler({
  duration,
  divisions,
}: {
  duration: number;
  divisions: number;
}) {
  return (
    <div
      className="vt-ruler"
      aria-hidden="true"
      style={{
        backgroundSize: `calc(100% / ${divisions * 5}) 4px, calc(100% / ${divisions}) 8px`,
      }}
    >
      {Array.from({ length: divisions + 1 }, (_, i) => (
        <span key={i} style={{ left: `${(100 * i) / divisions}%` }}>
          {pts((duration * i) / divisions)}
        </span>
      ))}
    </div>
  );
});

export function Timeline({ mediaUrl, duration, clock, frames, onSeek }: Props) {
  const root = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(720);
  useEffect(() => {
    const parent = root.current;
    if (!parent) return;
    const observer = new ResizeObserver(([entry]) =>
      setWidth(entry.contentRect.width),
    );
    observer.observe(parent);
    return () => observer.disconnect();
  }, []);
  const end = Number.isFinite(duration) && duration > 0 ? duration : 0;
  const now = Number.isFinite(clock) ? Math.max(0, Math.min(end, clock)) : 0;
  const divisions = Math.min(8, Math.max(2, Math.floor(width / 110)));
  const buckets = Math.min(48, Math.max(1, Math.floor(width / 24)));
  const events = useMemo(() => {
    const groups = new Map<
      number,
      { time: number; faces: number; count: number }
    >();
    if (end)
      for (const f of frames) {
        const time = f.media_pts_s;
        if (!f.faces.length || !Number.isFinite(time) || time < 0 || time > end)
          continue;
        const bin = Math.min(buckets - 1, Math.floor((time / end) * buckets));
        const event = groups.get(bin);
        if (event) {
          event.time = Math.min(event.time, time);
          event.faces += f.faces.length;
          event.count++;
        } else {
          groups.set(bin, { time, faces: f.faces.length, count: 1 });
        }
      }
    return Array.from(groups.values()).sort((a, b) => a.time - b.time);
  }, [frames, end, buckets]);

  return (
    <div
      ref={root}
      dir="ltr"
      className="vt-timeline"
      role="group"
      aria-label="خط زمان ویدیو"
    >
      <Ruler duration={end} divisions={divisions} />
      <div className="vt-track">
        <Filmstrip mediaUrl={mediaUrl} />
        <input
          className="vt-seek"
          type="range"
          dir="ltr"
          min="0"
          max={end}
          step="any"
          value={now}
          disabled={!end}
          aria-label="زمان پخش"
          aria-valuetext={`${pts(now)} / ${pts(end)}`}
          onChange={(event) => onSeek(Number(event.currentTarget.value))}
          onKeyDown={(event) => {
            const step = Math.min(0.25, end / 100);
            const seeks: Record<string, number> = {
              ArrowRight: now + step,
              ArrowLeft: now - step,
              ArrowUp: now + step,
              ArrowDown: now - step,
              PageUp: now + end / 10,
              PageDown: now - end / 10,
              Home: 0,
              End: end,
            };
            if (event.key in seeks) {
              event.preventDefault();
              onSeek(Math.max(0, Math.min(end, seeks[event.key])));
            }
          }}
        />
        <div className="vt-events">
          {events.map((event) => {
            const label = `${fa(event.faces)} مشاهده در ${fa(event.count)} فریم، از ${pts(event.time)}`;
            return (
              <button
                type="button"
                key={event.time}
                title={label}
                aria-label={label}
                style={{ left: `${(event.time / end) * 100}%` }}
                onClick={() => onSeek(event.time)}
              />
            );
          })}
        </div>
        {end > 0 && (
          <span
            className="vt-playhead"
            aria-hidden="true"
            style={{
              left: `clamp(0px, ${(now / end) * 100}%, calc(100% - 2px))`,
            }}
          />
        )}
      </div>
    </div>
  );
}
