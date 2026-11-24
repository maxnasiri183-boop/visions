import { Face, Frame, fa } from "./types";

export function FaceOverlay({
  frame,
  selected,
  fitted,
  onChoose,
}: {
  frame?: Frame;
  selected?: string;
  fitted: { width: number; height: number };
  onChoose: (p: Face) => void;
}) {
  return (
    <div className="overlay" style={fitted} dir="ltr">
      {frame?.faces.map((p, i) => (
        <button
          key={p.detection_id}
          data-detection-id={p.detection_id}
          className={"box " + (p.detection_id === selected ? "active" : "")}
          aria-pressed={p.detection_id === selected}
          aria-label={"انتخاب چهرهٔ " + fa(i + 1)}
          style={{
            left: (p.box[0] / frame.width) * 100 + "%",
            top: (p.box[1] / frame.height) * 100 + "%",
            width: (p.box[2] / frame.width) * 100 + "%",
            height: (p.box[3] / frame.height) * 100 + "%",
          }}
          onPointerDown={(event) => {
            if (!event.isPrimary || event.button !== 0) return;
            // Freeze the frame under the pointer before the next video frame replaces its box.
            event.preventDefault();
            event.currentTarget.focus({ preventScroll: true });
            onChoose(p);
          }}
          onClick={(event) => {
            if (event.detail === 0) onChoose(p);
          }}
        >
          <span>{fa(i + 1)}</span>
        </button>
      ))}
    </div>
  );
}
