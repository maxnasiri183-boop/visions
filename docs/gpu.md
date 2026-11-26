# GPU face detection

The GPU job uses the same FFmpeg feed, YuNet preprocessing, model checksum, confidence threshold, NMS and event format as the CPU worker. The GPU image uses the Java ONNX Runtime GPU artifact at 1.20.0, CUDA 12.8, and cuDNN 9. The NVIDIA kernel driver belongs on the host.

## Tesla T4 Review workspace

On the six-core i5-8400 with 15 GiB of system RAM, start the single-job Review workspace from the checkout. Docker needs access to NVIDIA device 0 through CDI. The launcher tests that access before building the images. On first use it prepares the demo assets and uses FFmpeg inside the Review image to generate the clip.

```sh
./tools/start_workspace.sh --t4
```

Open [Review](http://127.0.0.1:8091/) on the host. Choose **New run** and select frame **5** at **1×**. Expect one face and a verified crop. Start a second new run on frame **15** for a verified empty frame. Check the first inference in each new job with the following command.

```sh
grep -H 'ort_profile_cuda_kernels=' private/.review/review-*.log
```

The CUDA kernel count must be positive. The same line reports any CPU kernels. Replaying an existing observation can use its saved manifest and skip inference.

This profile starts Review, Kafka, MinIO and StarRocks. Review runs one Java detector job at a time on GPU 0 with reusable input buffers, a 512 MiB Java heap cap and two processors visible to the JVM. CUDA allocations and native memory are additional. These settings are a starting point for this host. The job logs record per-frame timing for measuring throughput locally.

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

## Input-buffer reuse

The CUDA job keeps one 640 × 640 RGB raster and one native-order planar BGR tensor per serial worker. The resize, channel order, pixel scale and model weights are shared with the CPU reference. The CPU job defaults to allocating its input per frame. Set `YUNET_REUSE_INPUT=false` on a GPU job to reproduce the original allocation path or `YUNET_REUSE_INPUT=true` on a CPU job to test buffer reuse. Each worker logs the effective setting.

The tuned CUDA image passed the speed-1 trace again with 106 CUDA kernels, zero CPU kernels and a pixel-exact crop for detection `a9ead396-f855-43d8-b774-1094ed37a4cc`. The CPU reference also passed with reuse disabled for detection `dd0a5e15-ca1d-499b-bbb2-afc1f18b9d2d`. Both replays left one logical SQL row. The sustained capacity comparison uses all frames and is available in the [benchmark results](benchmark.md).
