ARG WORKER_IMAGE=visions-detector:latest
ARG REVIEW_RUNTIME=cpu
FROM ${WORKER_IMAGE} AS worker
FROM python:3.12-slim-bookworm AS cpu
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libstdc++6 fonts-dejavu-core && rm -rf /var/lib/apt/lists/*
COPY --from=worker /opt/java/openjdk /opt/java/openjdk
COPY --from=worker /opt/worker /opt/worker
ENV JAVA_HOME=/opt/java/openjdk PATH=/opt/java/openjdk/bin:$PATH PYTHONUNBUFFERED=1

FROM worker AS cuda
RUN apt-get -o Dir::Etc::sourcelist=sources.list.d/ubuntu.sources -o Dir::Etc::sourceparts=- update \
    && apt-get install -y --no-install-recommends python3-venv fonts-dejavu-core \
    && python3 -m venv /opt/review-venv && rm -rf /var/lib/apt/lists/*
ENV PATH=/opt/review-venv/bin:$PATH PYTHONUNBUFFERED=1
ENTRYPOINT []

FROM ${REVIEW_RUNTIME} AS review
WORKDIR /app
COPY apps/review/pyproject.toml /build/
COPY apps/review/src /build/src
RUN pip install --no-cache-dir /build && rm -rf /build
COPY infra/starrocks/detections.sql infra/starrocks/detections.sql
ARG VISIONS_REVISION=unrecorded
LABEL org.opencontainers.image.revision=$VISIONS_REVISION
ENV VISIONS_REVISION=$VISIONS_REVISION PRIVATE=/app/private VISIONS_DETECTIONS_SQL=/app/infra/starrocks/detections.sql
CMD ["visions-review"]
