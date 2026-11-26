# RTSP cameras on the T4 VM

The simulator loops `feed/sample_videos/shib.mp4` and `shib2.mp4` at their original playback speed. Each publisher caches an H.264 copy under `private/simulator-cache/`. MediaMTX exposes `rtsp://rtsp:8554/shib` and `rtsp://rtsp:8554/shib2` on the existing Docker network. Both input videos and the cache are Git-ignored.

The recorder copies the incoming video track to two-second MP4 segments under `private/operator/recordings/<camera>/<session>/`. The T4 worker runs YuNet on finalized segments. The recognition worker compares detected faces with the published IRDB gallery. The live player targets eight seconds behind capture. When the worker falls behind, the recorder keeps the video and replaces queued inference with the latest segment. The UI distinguishes an unanalyzed frame from an analyzed frame containing zero faces.

Cleanup starts below 24 GiB free on the operator volume and stops at 30 GiB. It expires the oldest eligible recordings and their detection artifacts. Active work, pins, simulator inputs and enrollment originals are protected. If cleanup cannot keep up and free space falls below 1 GiB, capture pauses and retries automatically. Camera disconnects begin a new recording session on reconnect.

## Run on the prepared VM

From the repository root, bring up the existing backend, recognition and monitoring services first. The tracked T4 deployment overlays select its NVIDIA runtime and local images. The RTSP environment uses a distinct run name and camera map.

```dotenv
FEED_RUN_ID=t4-rtsp
RTSP_CAMERAS={"shib":"rtsp://rtsp:8554/shib","shib2":"rtsp://rtsp:8554/shib2"}
```

```sh
mkdir -p private/simulator-cache
docker compose -f compose.rtsp.yaml up -d
docker build --target worker --build-arg DETECTOR_IMAGE=visions-detector-gpu:latest \
  -t visions-feed-rtsp:local -f apps/feed/Dockerfile .
(cd operator && npm run build)
docker build -t visions-operator:detections -f operator/Dockerfile .
docker compose --env-file .env --env-file private/operator-rtsp.env \
  -f compose.feed.yaml -f compose.feed.monitoring.yaml \
  -f compose.feed.rtsp.yaml -f deploy/t4/feed.yaml up -d --no-build --wait
docker compose --env-file .env --env-file private/operator-rtsp.env \
  -f compose.operator.yaml -f deploy/t4/operator.yaml \
  up -d --no-build --no-deps --wait operator-ui recognition
```

For a revision-pinned rollout, save `VISIONS_REVISION`, `T4_DETECTOR_IMAGE`, `T4_REVIEW_IMAGE`, `T4_FEED_IMAGE`, `T4_OPERATOR_IMAGE` and `SIM_FFMPEG_IMAGE` in the private deployment environment. Use the full merged commit as the image tag and point the simulator at the tagged Review image. Pass this same environment to the backend, simulator, feed and operator Compose commands. Build the detector first, then Review, feed and operator. The T4 feed services share the detector runtime and installed feed package. Verify the live revision and browser assets with `tools/check_deployed_revision.sh "$VISIONS_REVISION"`.

The operator UI listens on VM loopback port 8092. Choose a camera in Live, or select a retained segment in Recordings. The operator dashboard in Grafana on port 13000 exposes retained duration, skipped frames, disk space and cleanup state.

To restore the previous file-feed deployment on this VM, use `private/operator-vm.env` and omit the overrides containing `rtsp`. Recreate the four feed services and the UI from their original local images. The runs have separate IDs. Stopping the simulator leaves the saved recordings intact.
