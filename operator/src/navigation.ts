import type { Route } from "./types";

type Moment = { occurrence_id: string; frame_index: number; detection_id: string };

export function recordingMoment(hit: Moment, source?: string): Route {
  return { view: "recordings", source, occ: hit.occurrence_id, frame: hit.frame_index, face: hit.detection_id };
}

export function routeUrl(route: Route): string {
  const query = new URLSearchParams();
  for (const [name, value] of Object.entries(route)) {
    if (value !== undefined) query.set(name, String(value));
  }
  return "?" + query;
}
