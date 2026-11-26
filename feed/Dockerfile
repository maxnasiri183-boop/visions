ARG REVIEW_IMAGE=visions-review:latest
ARG DETECTOR_IMAGE=visions-detector-gpu-operator:local
FROM ${REVIEW_IMAGE} AS python
COPY feed/*.py /feed/
COPY deploy/starrocks/detections.sql /feed/
WORKDIR /feed
ARG VISIONS_REVISION=unrecorded
LABEL org.opencontainers.image.revision=$VISIONS_REVISION
ENTRYPOINT ["python"]
CMD ["planner.py"]

FROM ${DETECTOR_IMAGE} AS worker
COPY --from=python /usr/local/bin/python3.12 /usr/local/bin/python3.12
COPY --from=python /usr/local/lib /usr/local/lib
COPY --from=python /feed /feed
RUN ldconfig && python3.12 -c 'import boto3,kafka,pymysql,sqlite3'
WORKDIR /feed
ARG VISIONS_REVISION=unrecorded
LABEL org.opencontainers.image.revision=$VISIONS_REVISION
ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["python3.12","worker.py"]
CMD []
