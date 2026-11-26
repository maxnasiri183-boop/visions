package visions;

import java.util.List;

public record Detection(
    int schema_version,
    String detection_id,
    String source_id,
    String asset_sha256,
    int video_track,
    int frame_index,
    long pts,
    List<Integer> time_base,
    List<Integer> frame_size,
    String frame_ingested_at,
    String detector_version,
    List<Integer> bbox_xywh,
    List<List<Double>> landmarks_xy,
    double confidence,
    String crop_uri,
    String crop_sha256) {}
