export type View = "live" | "recordings" | "search" | "gallery" | "health";
export type Route = {
  view: View;
  source?: string;
  occ?: string;
  frame?: number;
  face?: string;
  person?: string;
  version?: string;
  originOcc?: string;
  originFrame?: number;
  originFace?: string;
  originView?: "live" | "recordings";
};
export type Link = (patch: Route) => void;
export type Recording = {
  occurrence_id: string;
  source_id: string;
  asset_id: string;
  session_id: string;
  cycle: number;
  scheduled_at: number;
  started_at: number;
  finished_at: number | null;
  state: string;
  frame_count: number;
  duration_s: number;
  last_frame: number;
  synthetic: number;
};
export type Face = {
  detection_id: string;
  frame_index: number;
  confidence: number;
  box: number[];
  topic: string;
  partition_id: number;
  kafka_offset: number;
  latest_offset: number;
  sql_visible_at: number | null;
};
export type Frame = {
  frame_index: number;
  pts: number;
  time_base_num: number;
  time_base_den: number;
  media_pts_s: number;
  width: number;
  height: number;
  ingested_at: string;
  faces: Face[];
};
export type Decision = {
  state: string;
  reason: string;
  person_id?: string;
  display_name?: string;
  score?: number;
  threshold?: number;
  version?: string;
  model?: string;
  preprocessing?: string;
  candidate_person_id?: string;
  reference_id?: string;
  portrait_url?: string;
  face_url?: string;
};
export type Reference = {
  reference_id: string;
  person_id: string;
  display_name: string;
  status: string;
  reason?: string;
  portrait_url: string;
  session_id: string;
};
export type Version = {
  calibration?: { split_hashes?: Record<string, string> };
  version: string;
  created_at: number | string;
  threshold: number;
  model: string;
  preprocessing: string;
  reference_count: number;
  people_count: number;
};
export type Gallery = {
  active_version: string | null;
  versions: Version[];
  references: Reference[];
};
export const fa = (v: number, d = 0) =>
  Number(v).toLocaleString("fa-IR", { maximumFractionDigits: d });
export function date(v: number | string | null) {
  if (!v) return "—";
  const t = new Date(typeof v === "number" ? v * 1000 : v);
  return Number.isNaN(+t)
    ? "—"
    : t.toLocaleString("fa-IR", {
        timeZone: "Asia/Tehran",
        dateStyle: "short",
        timeStyle: "medium",
      });
}
export const pts = (s: number) =>
  fa(Math.floor(s / 60)) +
  ":" +
  Math.floor(s % 60).toLocaleString("fa-IR", { minimumIntegerDigits: 2 }) +
  "٫" +
  fa(Math.floor(s * 10) % 10);
export async function api<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    ...init,
    headers: { "X-Visions-Action": "operator", ...init?.headers },
    cache: "no-store",
  });
  if (!response.ok) {
    if (response.status === 410)
      throw Error("مدت نگهداری این مورد به پایان رسیده است.");
    if (response.status === 413) throw Error("حجم تصویر بیشتر از حد مجاز است.");
    throw Error("دریافت یا ثبت اطلاعات انجام نشد. دوباره تلاش کنید.");
  }
  return response.json();
}
export function post<T>(url: string, data: unknown, signal?: AbortSignal) {
  return api<T>(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
    signal,
  });
}
