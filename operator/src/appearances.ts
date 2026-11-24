export type Hit = {
  occurrence_id: string;
  frame_index: number;
  detection_id: string;
  media_pts_s: number;
  score: number;
  thumbnail_url: string;
  observed_utc?: string;
};
export type Group = {
  asset_id?: string;
  repeated?: boolean;
  occurrence_id: string;
  source_id: string;
  first_s: number;
  last_s: number;
  score: number;
  thumbnail_url: string;
  observations: Hit[];
};
export type Results = {
  groups: Group[];
  candidate_count: number;
  match_threshold: number;
};

export function bestObservation(group: Group) {
  return group.observations.reduce((best, current) =>
    best.score >= current.score ? best : current,
  );
}
