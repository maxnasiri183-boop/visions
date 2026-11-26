CREATE DATABASE IF NOT EXISTS visions;
USE visions;
CREATE TABLE IF NOT EXISTS face_detections (
    detection_id VARCHAR(36) NOT NULL,
    schema_version INT NOT NULL,
    source_id VARCHAR(128) NOT NULL,
    asset_sha256 VARCHAR(64) NOT NULL,
    video_track INT NOT NULL,
    frame_index BIGINT NOT NULL,
    pts BIGINT NOT NULL,
    time_base JSON NOT NULL,
    frame_size JSON NOT NULL,
    frame_ingested_at VARCHAR(40) NOT NULL,
    detector_version VARCHAR(128) NOT NULL,
    bbox_xywh JSON NOT NULL,
    landmarks_xy JSON NOT NULL,
    confidence DOUBLE NOT NULL,
    crop_uri VARCHAR(512) NOT NULL,
    crop_sha256 VARCHAR(64) NOT NULL
) PRIMARY KEY (detection_id)
DISTRIBUTED BY HASH(detection_id) BUCKETS 1
PROPERTIES ("replication_num"="1");
CREATE ROUTINE LOAD visions.load_detections ON face_detections
PROPERTIES ("format"="json", "strict_mode"="true", "max_error_number"="0",
            "max_batch_interval"="5", "desired_concurrent_number"="1")
FROM KAFKA ("kafka_broker_list"="kafka:9092", "kafka_topic"="face.detected.v1",
            "property.kafka_default_offsets"="OFFSET_BEGINNING");
