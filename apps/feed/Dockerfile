ARG DETECTOR_IMAGE=visions-detector-gpu-operator:local
FROM python:3.12-slim-bookworm AS package
COPY apps/feed/pyproject.toml /build/
COPY apps/feed/src /build/src
RUN pip install --no-cache-dir /build && rm -rf /build
COPY infra/starrocks/detections.sql /feed/
WORKDIR /feed
# Public Compose commands and site overrides invoke these filenames from /feed.
RUN for job in planner rtsp exporter soak worker health; do \
      printf 'from runpy import run_module\nrun_module("visions_feed.%s", run_name="__main__")\n' "$job" > "$job.py"; \
    done

FROM package AS python
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
ARG VISIONS_REVISION=unrecorded
LABEL org.opencontainers.image.revision=$VISIONS_REVISION
ENTRYPOINT ["python"]
CMD ["planner.py"]

FROM ${DETECTOR_IMAGE} AS worker
COPY --from=package /usr/local/bin/python3.12 /usr/local/bin/python3.12
COPY --from=package /usr/local/lib /usr/local/lib
COPY --from=package /feed /feed
RUN ln -sf python3.12 /usr/local/bin/python && ldconfig && python -c 'from visions_feed import worker'
WORKDIR /feed
ARG VISIONS_REVISION=unrecorded
LABEL org.opencontainers.image.revision=$VISIONS_REVISION
ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["python3.12","worker.py"]
CMD []
