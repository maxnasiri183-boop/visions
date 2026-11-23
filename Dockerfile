FROM gradle:8.14.3-jdk21 AS build
USER root
ENV GRADLE_USER_HOME=/tmp/gradle
WORKDIR /src
COPY . .
RUN --mount=type=cache,target=/tmp/gradle gradle --no-daemon --max-workers=2 :detection-worker:installDist

FROM eclipse-temurin:21-jre
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
COPY --from=build /src/detection-worker/build/install/detection-worker /opt/worker
ENTRYPOINT ["/opt/worker/bin/detection-worker"]
