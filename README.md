# Vision starter

A CPU-based Python sample for detecting faces with YuNet and comparing two aligned face embeddings with SFace. Detection returns pixel coordinates from the upper-left corner. Verification expects exactly one detected face per image and returns a cosine similarity and a match decision.

## File feed and StarRocks

The first Java 21 milestone replays a local clip at a configurable speed and traces one selected face through S3 and Kafka to a StarRocks primary-key row. The acceptance runner replays the same observation and compares the stored crop pixel by pixel. [راهنمای فارسی اجرا و بازبینی](docs/first-feed.fa.md)

## Run the Python sample

From this directory with Python 3.10 or newer, run the following commands. Internet access is needed to fetch models and sample photos on first use.

```sh
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python samples.py huggingface
python vision.py detect samples/huggingface/reference.jpg
python vision.py verify samples/huggingface/reference.jpg samples/huggingface/same.jpg
python vision.py verify samples/huggingface/reference.jpg samples/huggingface/different.jpg
```

On Windows, activate with `.venv\Scripts\activate`. Use the filenames printed by the sampler if their extensions differ.

For the Kaggle example, run `python samples.py kaggle` and use the files in `samples/kaggle/` with the same commands. KaggleHub downloads the dataset to its cache before the sampler selects three photos. Each source contains a version of Labeled Faces in the Wild, so samples from the two sources may overlap. These examples demonstrate inference. To measure performance, use held-out labeled pairs and report false matches and missed matches at the chosen threshold.

## Data and models

- https://huggingface.co/datasets/marcelohaps/lfw
- https://www.kaggle.com/datasets/jessicali9530/lfw-dataset
- https://huggingface.co/opencv/face_detection_yunet
- https://huggingface.co/opencv/face_recognition_sface

Use the --threshold option on verify to adjust the default cosine cutoff of 0.363. A similarity score is not a probability.

## Browser workspace

Play the fixed clip, inspect a frame and its stored crop, trace an observation through Kafka and SQL, or replay it from the [Review workspace](docs/review.md).

The [Operations guide](docs/operations.md) covers live pipeline rates, ingestion backlog, stage timings and Grafana. The [A100 profile](docs/gpu.md) verifies the execution provider inside the container. The [sustained benchmark](docs/benchmark.md) measures complete labeled feeds across simultaneous streams.

## Persian video operator pilot

Watch a private source, inspect a face against a versioned reference gallery, or find historical video appearances from an independent photo. Use the SSH tunnel and the prepared-server startup command in the [operator guide](docs/operator.md).

## Portrait-to-video and production-rate demo

Open a real enrollment portrait, follow a matching face into its recording, inspect the frozen decision and SQL evidence, then change the source speed and watch Kafka and SQL rates in Grafana. The [laptop walkthrough and deployment runbook](docs/portrait-replay.md) includes the SSH command, a separate 300,000-person synthetic browsing fixture and release verification.

The active research gallery contains 28 enrolled people from VidTIMIT. The private import adds 102 independently recorded studio clips. The [corpus and evaluation report](docs/vidtimit43.md) lists download and publication commands, the frozen threshold, and held-out results including false identifications.
