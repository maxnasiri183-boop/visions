"""Detect faces in a photo or verify a pair of portraits."""
import argparse
import json
from pathlib import Path


def model(repo, filename):
    from huggingface_hub import hf_hub_download
    return hf_hub_download("opencv/" + repo, filename)


def detect(path):
    import cv2
    image = cv2.imread(str(path))
    if image is None:
        raise ValueError(f"Cannot read {path}")
    height, width = image.shape[:2]
    detector = cv2.FaceDetectorYN.create(
        model("face_detection_yunet", "face_detection_yunet_2023mar.onnx"),
        "", (width, height))
    _, faces = detector.detect(image)
    return image, [] if faces is None else faces


def verify(first, second, threshold):
    import cv2
    recognizer = cv2.FaceRecognizerSF.create(
        model("face_recognition_sface", "face_recognition_sface_2021dec.onnx"), "")
    features = []
    for path in (first, second):
        image, faces = detect(path)
        if len(faces) != 1:
            raise ValueError(f"{path} contains {len(faces)} detected faces. Supply one portrait per file.")
        aligned = recognizer.alignCrop(image, faces[0])
        features.append(recognizer.feature(aligned).copy())
    score = float(recognizer.match(*features, cv2.FaceRecognizerSF_FR_COSINE))
    return dict(cosine_similarity=score, threshold=threshold, match=score >= threshold)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("detect").add_argument("photo", type=Path)
    pair = commands.add_parser("verify")
    pair.add_argument("first", type=Path)
    pair.add_argument("second", type=Path)
    pair.add_argument("--threshold", type=float, default=0.363)
    args = parser.parse_args()
    try:
        if args.command == "detect":
            _, faces = detect(args.photo)
            result = [dict(x=int(f[0]), y=int(f[1]), width=int(f[2]),
                           height=int(f[3]), confidence=float(f[-1])) for f in faces]
        else:
            if not -1 <= args.threshold <= 1:
                parser.error("threshold must be between -1 and 1")
            result = verify(args.first, args.second, args.threshold)
    except ValueError as error:
        parser.exit(2, f"{error}\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
