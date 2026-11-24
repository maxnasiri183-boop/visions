FROM visions-detector:latest AS worker
FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libstdc++6 fonts-dejavu-core && rm -rf /var/lib/apt/lists/*
COPY --from=worker /opt/java/openjdk /opt/java/openjdk
COPY --from=worker /opt/worker /opt/worker
ENV JAVA_HOME=/opt/java/openjdk PATH=/opt/java/openjdk/bin:$PATH PYTHONUNBUFFERED=1
WORKDIR /app
COPY tools/requirements-review.txt web/requirements.txt ./
RUN pip install --no-cache-dir -r requirements-review.txt -r requirements.txt
COPY tools/acceptance.py tools/acceptance.py
COPY deploy/detections.sql deploy/detections.sql
COPY web/*.py web/
COPY web/static web/static
ENV PYTHONPATH=/app/tools:/app/web
CMD ["python","web/app.py"]
