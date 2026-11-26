import { Pause, Play } from "lucide-react";

// Only a presented-frame callback measures the frame on screen. The fallback
// advances transport controls but cannot establish a measured appearance box.
export function observePlayback(
  video: HTMLVideoElement,
  display: (seconds: number, presented: boolean) => void,
) {
  if (typeof video.requestVideoFrameCallback === "function") {
    const tick: VideoFrameRequestCallback = (_, frame) => {
      ticket = video.requestVideoFrameCallback(tick);
      display(frame.mediaTime, true);
    };
    let ticket = video.requestVideoFrameCallback(tick);
    return () => video.cancelVideoFrameCallback(ticket);
  }
  const update = () => display(video.currentTime, false);
  video.addEventListener("timeupdate", update);
  return () => video.removeEventListener("timeupdate", update);
}

export function PlayButton({ playing, toggle }: { playing: boolean; toggle: () => void }) {
  const label = playing ? "توقف" : "پخش";
  return (
    <button className="icon-only" aria-label={label} title={label} onClick={toggle}>
      {playing ? <Pause /> : <Play />}
    </button>
  );
}
