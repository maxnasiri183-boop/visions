const el = (id) => document.getElementById(id);
const video = el("video");
let runs = [],
  source = null,
  frame = 5,
  selected = false,
  posting = false,
  last = "";
const current = () => runs.find((r) => r.source === source);
function message(text) {
  el("message").hidden = !text;
  el("message").textContent = text;
}
function seek(index) {
  video.pause();
  frame = Math.max(0, Math.min(19, index));
  video.currentTime = (frame + 0.1) / 10;
  render();
}
function frameStatus(run, visible) {
  if (frame !== run.frame) return "Frame not processed.";
  if (visible)
    return selected ? "" : "Click the face box to inspect this observation.";
  if (run.status === "error") return "Processing error.";
  if (run.status === "live") return "Result pending.";
  return "Processed with zero faces.";
}
function render() {
  const run = current(),
    report = run?.report || {},
    ev = report.event;
  el("frame").textContent = "Frame " + frame + " / 19";
  el("slider").value = frame;
  el("time").textContent = (frame / 10).toFixed(2) + " s";
  el("prev").disabled = !frame;
  el("next").disabled = frame === 19;
  el("play").textContent = video.paused ? "Play" : "Pause";
  el("still").hidden = !video.paused;
  if (video.paused) {
    const original =
      run && frame === run.frame && Object.keys(report).length
        ? "/api/runs/" + source + "/frame"
        : "/media/" + frame + ".png";
    if (el("still").getAttribute("src") !== original)
      el("still").src = original;
  }
  const visible = ev && ev.frame_index === frame;
  const frameReady =
    !video.paused || (el("still").complete && el("still").naturalWidth > 0);
  el("box").hidden = !visible || !frameReady;
  if (visible) {
    const [x, y, w, h] = ev.bbox_xywh;
    Object.assign(el("box").style, {
      left: (x / 512) * 100 + "%",
      top: (y / 512) * 100 + "%",
      width: (w / 512) * 100 + "%",
      height: (h / 512) * 100 + "%",
    });
  }
  if (!run) return;
  const playbackRate = report.speed ?? run.speed ?? 1;
  if (video.playbackRate !== playbackRate) video.playbackRate = playbackRate;
  el("status").textContent = {
    saved: "Saved run",
    live: "Live job",
    error: "Job error",
  }[run.status];
  el("stage").textContent =
    run.status === "saved"
      ? "Inference selected frame " +
        run.frame +
        " of 20 · " +
        report.speed +
        "× speed"
      : run.stage;
  const inspecting = visible && selected;
  el("hint").textContent = frameStatus(run, visible);
  el("detail").hidden = !inspecting;
  const busy = posting || runs.some((r) => r.status === "live");
  el("new").disabled = busy;
  el("replay").disabled = busy;
  if (!inspecting) return;
  const url = "/api/runs/" + source + "/crop";
  if (el("crop").getAttribute("src") !== url) el("crop").src = url;
  el("confidence").textContent = (ev.confidence * 100).toFixed(2) + "%";
  el("identity").value = ev.detection_id;
  el("fields").replaceChildren();
  for (const [k, v] of Object.entries({
    Source: source,
    "Frame / media time":
      frame +
      " / " +
      ((ev.pts * ev.time_base[0]) / ev.time_base[1]).toFixed(3) +
      " s",
    "Box (x, y, w, h)": ev.bbox_xywh.join(", ") + " px",
    "Kafka topic": report.topic,
    "Partition / offset": report.partition + " / " + report.offsets[0],
    "First SQL visibility": report.seconds_to_first_visible.toFixed(3) + " s",
  })) {
    const div = document.createElement("div"),
      dt = document.createElement("dt"),
      dd = document.createElement("dd");
    dt.textContent = k;
    dd.textContent = v;
    div.append(dt, dd);
    el("fields").append(div);
  }
  el("row").textContent = JSON.stringify(report.sql, null, 2);
  const offsets = [...report.offsets.slice(1), ...(run.replay_offsets || [])];
  el("evidence").textContent = offsets.length
    ? "Replay offset " +
      offsets.at(-1) +
      " · " +
      (run.replay_rows ?? report.logical_rows_after_replay) +
      " logical row after replay."
    : "This observation has not been replayed.";
}
async function refresh() {
  const response = await fetch("/api/runs", { cache: "no-store" });
  if (!response.ok) throw Error("Runs unavailable");
  const data = await response.json();
  runs = data;
  if (!source && data.length) {
    const query = new URLSearchParams(location.search);
    const hit = data.find(
      (r) =>
        query.has("detection_id") &&
        r.report.event?.detection_id === query.get("detection_id"),
    );
    const run =
      hit ||
      data.find((r) => r.source === query.get("run")) ||
      data.find((r) => r.source === "review-6c19e65ad6ee") ||
      data.find((r) => r.report.event) ||
      data[0];
    if (query.has("detection_id") && !hit)
      message("Detection ID not found in saved Review runs.");
    source = run.source;
    selected = Boolean(hit);
    seek(run.frame);
  }
  const version = JSON.stringify(data);
  if (last !== version) {
    el("runs").replaceChildren(
      ...data.map(
        (r) =>
          new Option(
            (r.status === "live"
              ? "Live · "
              : r.status === "error"
                ? "Error · "
                : "Saved · ") + r.source,
            r.source,
          ),
      ),
    );
    el("runs").value = source;
    last = version;
    render();
  }
}
function changeRun(id) {
  source = id;
  selected = false;
  const run = current();
  if (run) seek(run.frame);
  history.replaceState(null, "", "/?run=" + encodeURIComponent(id));
}
el("runs").onchange = (e) => changeRun(e.target.value);
el("still").onload = render;
el("still").onerror = () => message("Original frame unavailable.");
el("crop").onerror = () =>
  message("Stored crop unavailable. Check object storage in Operations.");
el("video").onpause = render;
el("video").onplay = render;
function displayed(_, meta) {
  if (!video.paused) {
    frame = Math.min(19, Math.round(meta.mediaTime * 10));
    render();
  }
  video.requestVideoFrameCallback(displayed);
}
if (video.requestVideoFrameCallback) video.requestVideoFrameCallback(displayed);
else
  video.ontimeupdate = () => {
    if (!video.paused) {
      frame = Math.min(19, Math.floor(video.currentTime * 10));
      render();
    }
  };
el("play").onclick = async () => {
  if (!video.paused) video.pause();
  else {
    if (frame === 19) seek(0);
    await video.play().catch(() => message("Video playback failed."));
  }
};
el("slider").oninput = (e) => seek(Number(e.target.value));
el("prev").onclick = () => seek(frame - 1);
el("next").onclick = () => seek(frame + 1);
el("box").onclick = () => {
  selected = true;
  seek(current().report.event.frame_index);
};
el("copy").onclick = async () => {
  try {
    await navigator.clipboard.writeText(el("identity").value);
    el("copy").textContent = "Copied";
    setTimeout(() => (el("copy").textContent = "Copy"), 1000);
  } catch {
    el("identity").select();
  }
};
el("new").onclick = () => el("form").showModal();
el("form").onclose = () => {
  if (el("form").returnValue === "start")
    submit({
      action: "start",
      speed: Number(el("speed").value),
      frame: Number(el("target").value),
    });
};
el("replay").onclick = () => submit({ action: "replay", source });
async function submit(body) {
  posting = true;
  render();
  message("");
  try {
    const res = await fetch("/api/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Review-Action": "job" },
      body: JSON.stringify(body),
    });
    if (!res.ok) throw Error("Job request failed (" + res.status + ")");
    const job = await res.json();
    await refresh();
    if (body.action === "start") changeRun(job.source);
  } catch (e) {
    message(e.message);
  } finally {
    posting = false;
    render();
  }
}
async function poll() {
  try {
    await refresh();
  } catch {
    message("Connection lost. Retrying…");
  }
  setTimeout(poll, 1500);
}
poll();
