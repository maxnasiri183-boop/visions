"""Fetch three labeled portraits for a positive and a negative comparison."""
import argparse
from pathlib import Path
from shutil import copyfile


def candidates(source):
    if source == "huggingface":
        from huggingface_hub import HfApi, hf_hub_download
        repo = "marcelohaps/lfw"
        files = HfApi().list_repo_files(repo, repo_type="dataset")
        return ([Path(f) for f in files if f.startswith("train/")],
                lambda p: hf_hub_download(repo, p.as_posix(), repo_type="dataset"))
    import kagglehub
    root = Path(kagglehub.dataset_download("jessicali9530/lfw-dataset"))
    return root.rglob("*.jpg"), lambda p: p


def choose(paths):
    groups = {}
    for path in sorted(paths):
        if path.suffix.lower() in {".jpg", ".jpeg", ".png"}:
            groups.setdefault(path.parent, []).append(path)
    for person, photos in groups.items():
        if len(photos) >= 2 and len(groups) >= 2:
            other = next(items[0] for key, items in groups.items() if key != person)
            return [photos[0], photos[1], other]
    raise ValueError("Expected portrait folders with at least one same-person pair.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", choices=["huggingface", "kaggle"])
    source = parser.parse_args().source
    paths, fetch = candidates(source)
    output = Path("samples") / source
    output.mkdir(parents=True, exist_ok=True)
    for name, path in zip(["reference", "same", "different"], choose(paths)):
        target = output / (name + path.suffix.lower())
        copyfile(fetch(path), target)
        print(path.parent.name, target)
