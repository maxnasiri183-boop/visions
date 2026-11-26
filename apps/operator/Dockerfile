FROM python:3.12-slim-bookworm AS operator
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 OPERATOR_DATA=/data OPERATOR_ROOT=/data IDENTITY_YUNET=/models/yunet.onnx OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
WORKDIR /srv
COPY apps/operator/pyproject.toml /build/
COPY apps/operator/src /build/src
RUN test -s /build/src/visions_operator/dist/index.html && pip install --no-cache-dir /build && rm -rf /build
# Older private Compose overrides invoke this file with python from /srv.
RUN mkdir -p operator && printf 'from visions_operator.server import main\nmain()\n' > operator/server.py
ARG VISIONS_REVISION=unrecorded
LABEL org.opencontainers.image.revision=$VISIONS_REVISION
ENV VISIONS_REVISION=$VISIONS_REVISION
CMD ["visions-operator"]

FROM operator AS research
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
COPY research/identity /build/
RUN pip install --no-cache-dir /build && rm -rf /build

FROM operator AS runtime
