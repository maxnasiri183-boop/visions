# A100 detector job

The GPU job uses the same FFmpeg feed, YuNet preprocessing, model checksum, confidence threshold, NMS and event format as the CPU worker. The GPU image uses the Java ONNX Runtime GPU artifact at 1.20.0, CUDA 12.8, and cuDNN 9. The NVIDIA kernel driver belongs on the host.

## Verify the host and container

On the tested Ubuntu 24.04 host, the installed driver needed its module loaded. The device is an NVIDIA A100-SXM4-40GB with 40,960 MiB and driver 595.99.02. Docker Engine 29.1.3 supports native CDI. Install the NVIDIA Container Toolkit base package from NVIDIA's signed apt repository, then check the following commands.

```sh
sudo modprobe nvidia
sudo modprobe nvidia_uvm
nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv
nvidia-ctk cdi list
docker compose -f compose.yaml -f compose.gpu.yaml build detector-gpu
docker compose -f compose.yaml -f compose.gpu.yaml run --rm --no-deps \
  --entrypoint nvidia-smi detector-gpu -L
```

If the CDI list is empty after a driver update, run `sudo systemctl restart nvidia-cdi-refresh.service`. The service regenerates the device specification consumed by Docker.

## Verify actual model execution

Use the fixture and initialized backend services from the acceptance setup. Each new source gets its own observations. Reusing the same source replays a saved frame manifest and can skip inference entirely.

```sh
docker compose -f compose.yaml -f compose.gpu.yaml run --rm --no-deps detector-gpu \
  /input/feed.mkv "gpu-check-$(date +%s)" 5 1
```

The worker profiles the first real inference in each GPU session, counts CUDA and CPU operator executions, and prints both totals. The actual ORT session fails on an unavailable CUDA provider. The profile check fails on zero CUDA kernels and logs any CPU kernel execution. Profiling stops after that first inference. The CPU service remains selectable as `docker compose run --rm detector`.

For the tested CUDA/cuDNN combination and CDI setup, see the [ONNX Runtime compatibility table](https://onnxruntime.ai/docs/execution-providers/CUDA-ExecutionProvider.html), [NVIDIA installation instructions](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html), and [CDI documentation](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/cdi-support.html).

## Tested trace

The speed-1 GPU acceptance run on the A100 executed 106 CUDA kernels and zero CPU kernels in the first profiled frame. Detection confidence was 0.943449 and the box was (178, 63, 92, 112), with IoU 0.836224 against the manual label. The crop pixels matched. Kafka offsets 194 and 195 carried identical payloads for detection `5604ea0e-bc67-4653-923b-558734fcac3b`. One logical row remained after replay. The first visible SQL row was observed after 7.196 seconds. This cold single-frame run includes downstream storage and Routine Load delays.

```sh
COMPOSE_FILE=compose.yaml:compose.gpu.yaml DETECTOR_SERVICE=detector-gpu \
  .venv/bin/python tools/acceptance.py --speed 1
```
