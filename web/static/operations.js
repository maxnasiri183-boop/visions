"use strict";
const $ = id => document.getElementById(id);
const number = (n, digits = 1) => Number.isFinite(n) ? n.toLocaleString("en-US", {maximumFractionDigits: digits}) : "Unavailable";
const unit = (n, name, digits = 1) => Number.isFinite(n) ? number(n, digits) + " " + name : "Unavailable";
const utc = t => new Date(t * 1000).toISOString().slice(11, 19) + " UTC";
const stageNames = {
  decode_ms: "Decoder pipe read", preprocess_ms: "Input preprocessing", inference_ms: "ONNX inference",
  crop_upload_ms: "Crop upload", manifest_io_ms: "Frame manifest I/O", kafka_publish_ms: "Kafka publication"
};
let current;

function text(id, content) { $(id).textContent = content; }
function element(tag, content) {
  const node = document.createElement(tag);
  node.textContent = content;
  return node;
}
function rows(id, data, empty, cols) {
  const body = $(id);
  body.replaceChildren();
  for (const row of data) {
    const tr = document.createElement("tr");
    row.forEach(cell => tr.append(element("td", cell)));
    body.append(tr);
  }
  if (!data.length) {
    const td = element("td", empty);
    td.colSpan = cols;
    td.className = "empty";
    const tr = document.createElement("tr");
    tr.append(td);
    body.append(tr);
  }
}
function details(id, pairs) {
  $(id).replaceChildren(...pairs.map(([label, value]) => {
    const row = document.createElement("div");
    row.append(element("dt", label), element("dd", value));
    return row;
  }));
}
function stageDetails() {
  const stream = current.streams.find(row => key(row) === $("stage-slot").value);
  text("stage-note", stream ? "Mean over the last 5 s of this stream. Crop and publication include detections only. Sampled at " + utc(stream.at) + "." : "Awaiting instrumented feed samples.");
  const fields = Object.entries(stageNames).map(([field, label]) => [label, unit(stream?.stages[field], "ms", 2)]);
  if (stream?.provider === "cuda") {
    const proof = stream.profile;
    fields.push(["First inference · profiled kernels", proof ? proof.cuda + " CUDA / " + proof.cpu + " CPU" : "Unavailable"]);
    if (proof?.cpu) fields.push(["Provider fallback", "CPU operators in GPU profile"]);
  }
  details("stages", fields);
}
const key = row => row.run + "/" + row.slot;
function render(data) {
  current = data;
  const active = data.streams.filter(row => row.active);
  const liveJobs = data.jobs.filter(j => j.status === "live");
  const lagging = active.some(row => row.lag_ms > 1000 / row.input_fps);
  const serviceIssue = Object.values(data.services).some(service => !service.up) || data.load?.state !== "RUNNING";
  text("connection", "● Connected");
  $("connection").className = "";
  text("sample", "Updated " + utc(data.at) + " · Refreshes every 2 seconds");
  text("health", serviceIssue ? "Service issue" : active.length ? (lagging ? "Behind" : "On pace") : liveJobs.length ? "Review running" : "Idle");
  text("health-note", active.length ? active.length + " active stream(s) · one-frame lateness threshold" : liveJobs.length ? liveJobs[0].stage : "No recent worker samples");
  text("rate", active.length ? unit(active.some(r => r.inference_fps === null) ? null : active.reduce((n,r) => n+r.inference_fps,0), "FPS") : "—");
  text("rate-note", "All-frame workers · 5 s trailing rate");
  text("p95", unit(data.latency.p95, "s", 2));
  text("latency-note", "p50 " + unit(data.latency.p50,"s",2) + " · " + number(data.latency.count,0) + " observations in 5 min");
  const gpu = data.host.gpu;
  text("util", unit(gpu?.util, "%", 0));
  const cudaStreams = active.filter(stream => stream.provider === "cuda");
  const verified = cudaStreams.filter(stream => stream.profile?.cuda > 0).length;
  const fallback = cudaStreams.some(stream => stream.profile?.cpu > 0);
  const gpuStatus = fallback ? "CPU fallback detected" : cudaStreams.length ? verified + " / " + cudaStreams.length + " active workers verified on CUDA" : "No active CUDA benchmark streams";
  text("gpu-note", (gpu ? gpu.name + " · " + number(gpu.used/1024,1) + " / " + number(gpu.total/1024,1) + " GiB" : "Sampler unavailable") + " · " + gpuStatus);
  text("streams-note", "Five-second frame completion rates. Streams without a sample for 5 s are marked stale.");
  rows("streams", data.streams.map(r => [
    r.run + " / " + r.slot, [r.provider.toUpperCase(),r.warmup ? "warm-up" : "",r.active ? "live" : "stale"].filter(Boolean).join(" · "),
    number(r.input_fps),number(r.decoded_fps),number(r.inference_fps),number(r.detection_rate),number(r.frames,0),
    r.skipped + " / " + r.dropped, number(r.media_s,2), number(r.lag_ms,1)]),
    "No sustained stream samples in the last five minutes.",10);
  text("jobs",data.jobs.map(j => j.source + " · " + j.status + " · " + j.stage).join(" | "));
  const select = $("stage-slot");
  const previous = select.value;
  const nextKeys = data.streams.map(key);
  if (nextKeys.join(" ") !== Array.from(select.options).map(option => option.value).join(" ")) {
    select.replaceChildren(...data.streams.map(r => new Option(r.run + " / " + r.slot, key(r))));
    if (nextKeys.includes(previous)) select.value = previous;
  }
  select.disabled = !data.streams.length;
  stageDetails();
  const load = data.load || {};
  details("ingest",[
    ["Routine Load",load.state || "Unavailable"],["Unconsumed records",number(data.lag,0)],
    ["Kafka next offset",number(data.end_offset,0)],["Last applied offset",load.offsets?.["0"] ?? "Unavailable"],
    ["Loaded / rejected rows",number(load.loaded,0) + " / " + number(load.rejected,0)],
    ["Active tasks",number(load.tasks,0)],["Last state error",load.state ? (load.last_error || "None reported") : "Unavailable"]
  ]);
  details("host",[
    ...Object.entries(data.services).map(([service,status]) => [service,status.up ? "Ready" : "Unavailable · " + status.error]),
    ["Review API","Ready"],
    ...Object.entries(data.host.containers || {}).sort().map(([name,entry]) =>
      ["Container · "+name, entry.health === "none" ? entry.state : entry.state+" / "+entry.health]),
    ...(!Object.keys(data.host.containers || {}).length ? [["Container health","Sampler unavailable or stale"]] : []),["CPU busy",unit(data.host.cpu,"%")],
    ["Memory used / total",number(data.host.ram_used / 2**30) + " / " + number(data.host.ram_total / 2**30) + " GiB"],
    ["Private volume free",unit(data.host.disk_free / 2**30,"GiB")],
    ["Telemetry read errors",number(data.telemetry_errors,0)]
  ]);
  const last = data.last_review;
  const review = $("last-review");
  review.replaceChildren();
  if (last) {
    const link = element("a",last.source);
    link.href = "/?detection_id=" + encodeURIComponent(last.id);
    review.append("Latest saved detection · ",link,document.createElement("br"),
      last.decoded + " decoded · " + last.inferred + " selected · " + (last.decoded-last.inferred) + " intentionally skipped · " + unit(last.latency, "s",3) + " to SQL.");
  }
  rows("history",data.history.map(run => [
    run.start_utc.slice(0,19).replace("T"," "),run.provider.toUpperCase(),run.streams,run.measured_frames,
    number(run.duration_s),number(run.target_fps),number(run.inference_fps),run.dropped_frames,
    number(run.sql_latency_s.p95,2),number(run.quality_min_iou,3)]),"No completed benchmarks.",10);
}
async function refresh() {
  try {
    const response = await fetch("/api/operations",{cache:"no-store",signal:AbortSignal.timeout(12000)});
    if (!response.ok) throw Error("HTTP " + response.status);
    render(await response.json());
  } catch (error) {
    text("connection","● Disconnected");
    $("connection").className = "bad";
    text("sample",current ? "Stale · Last successful sample " + utc(current.at) : "Measurements unavailable.");
  } finally {
    setTimeout(refresh,2000);
  }
}
$("stage-slot").addEventListener("change",stageDetails);
refresh();
