FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 OPERATOR_DATA=/data OPERATOR_ROOT=/data IDENTITY_YUNET=/models/yunet.onnx OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
WORKDIR /srv
COPY operator/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY identity identity
COPY operator/server.py operator/server.py
COPY operator/dist operator/dist
ENV PYTHONPATH=/srv
CMD ["python","operator/server.py"]
