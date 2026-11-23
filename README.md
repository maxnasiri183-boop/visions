# Vision starter

A CPU-based Python sample for detecting faces with YuNet and comparing two aligned face embeddings with SFace. Detection returns pixel coordinates from the upper-left corner. Verification expects exactly one detected face per image and returns a cosine similarity and a match decision.

## Run

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
