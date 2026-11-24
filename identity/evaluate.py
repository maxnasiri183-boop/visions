"""Audit capture separation, freeze on calibration, then score independent trial units."""

import argparse
from collections import Counter, defaultdict
from hashlib import sha256
import json
import math
import time

import cv2
import numpy as np

from . import core as c
from .corpus import dump
from .search import search
from .worker import attached

BASE = c.ROOT / "research"
SPLITS = ("enrollment", "stream", "upload", "calibration", "evaluation")


def manifest(split):
    return json.loads((BASE / (split + ".json")).read_text())


def fingerprint(frame):
    thumb = cv2.resize(
        cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (32, 32), interpolation=cv2.INTER_AREA
    )
    dct = cv2.dct(thumb.astype(np.float32))[:8, :8]
    return int.from_bytes(np.packbits(dct > np.median(dct)).tobytes(), "big"), thumb


def audit(extended=False):
    previous = []
    issues = []
    details = {}
    split_digests = {}
    for split in SPLITS:
        entries = manifest(split)
        if extended and split == "stream":
            entries += manifest("stream-extension")
        split_digests[split] = sha256(
            (BASE / (split + ".json")).read_bytes()
        ).hexdigest()
        hashes, sessions, images = set(), set(), []
        for row in entries:
            p = c.checked_path(row["path"])
            digest = sha256(p.read_bytes()).hexdigest()
            if digest != row["sha256"]:
                raise ValueError("Checksum differs for " + row["path"])
            hashes.add(digest)
            sessions.update(row["sessions"])
            if p.suffix.lower() == ".mkv":
                cap = cv2.VideoCapture(str(p))
                count = 0
                while True:
                    ok, image = cap.read()
                    if not ok:
                        break
                    images.append(fingerprint(image))
                    count += 1
                cap.release()
                if count != row["frames"]:
                    raise ValueError("Corpus frame count differs for " + p.name)
            else:
                images.append(fingerprint(c.upload_image(p.read_bytes())))
        for role, earlier_hashes, earlier_sessions, fingerprints in previous:
            near = sum(
                1
                for x, im in images
                for y, other in fingerprints
                if (x ^ y).bit_count() <= 6
                and np.abs(im.astype(float) - other).mean() <= 2
            )
            if hashes & earlier_hashes or sessions & earlier_sessions or near:
                issues.append(
                    {
                        "roles": [role, split],
                        "identical": len(hashes & earlier_hashes),
                        "shared_capture_sessions": len(sessions & earlier_sessions),
                        "near_duplicates": near,
                    }
                )
        previous.append((split, hashes, sessions, images))
        details[split] = {
            "assets": len(entries),
            "frames_checked": len(images),
            "sessions": len(sessions),
        }
    result = {
        "splits": details,
        "split_hashes": split_digests,
        "cross_split_issues": issues,
        "near_duplicate_rule": "64-bit DCT Hamming <=6 and 32x32 grayscale mean absolute difference <=2/255",
    }
    if extended:
        result["extension_hash"] = sha256(
            (BASE / "stream-extension.json").read_bytes()
        ).hexdigest()
    dump(
        BASE / ("extended-split-audit.json" if extended else "split-audit.json"), result
    )
    if issues:
        raise ValueError(
            "Cross-split leakage detected; inspect private split-audit.json"
        )
    return result


def encode_query(row):
    image = c.upload_image(c.checked_path(row["path"]).read_bytes())
    faces = c.detect(image)
    if len(faces) != 1:
        return None, f"Expected one face, detected {len(faces)}"
    return c.features(image, faces[0])


def select_occurrences(db, extended=False):
    catalog = json.loads((c.ROOT / "catalog.json").read_text())
    clips = manifest("stream") + (manifest("stream-extension") if extended else [])
    lookup = {r["path"]: r for r in clips}
    chosen = {}
    for asset in catalog:
        if asset["path"] not in lookup:
            continue
        # A single completed occurrence for each underlying asset avoids inflating evidence with loops.
        row = db.execute(
            """SELECT u.occurrence_id FROM feed.occurrences u WHERE u.asset_sha256=?
           AND u.state='complete' AND NOT EXISTS(SELECT 1 FROM feed.observations o LEFT JOIN indexed i
           USING(detection_id) WHERE o.occurrence_id=u.occurrence_id AND i.detection_id IS NULL)
           ORDER BY u.finished_at DESC LIMIT 1""",
            (asset["sha256"],),
        ).fetchone()
        if not row:
            raise ValueError(
                "Waiting for completed and indexed corpus asset " + asset["asset_id"]
            )
        chosen[row[0]] = dict(**lookup[asset["path"]], asset_id=asset["asset_id"])
    if len(chosen) != len(lookup):
        raise ValueError("Corpus catalog is incomplete")
    return chosen


def label(occ, obs, width):
    people = occ["people"]
    if len(people) == 1:
        return people[0]
    if len(people) == 2:
        x, _, w, _ = json.loads(obs["bbox"])
        return people[int(x + w / 2 >= width / 2)]
    return None


def rows(db, occs):
    items = {}
    for oid, occ in occs.items():
        for obs in db.execute(
            """SELECT i.*,f.width,o.event_json FROM indexed i JOIN feed.frames f USING(occurrence_id,frame_index)
                                JOIN feed.observations o USING(detection_id) WHERE i.occurrence_id=?""",
            (oid,),
        ):
            items[obs["detection_id"]] = (obs, label(occ, obs, obs["width"]))
    return items


def rank_id(vec, refs):
    if vec is None:
        return None, -1
    return max(
        ((r["person_id"], c.dot(vec, r["embedding"])) for r in refs), key=lambda x: x[1]
    )


def freeze():
    proof = audit()
    with c.connect() as db:
        attached(db)
        occs = select_occurrences(db)
        indexed = rows(db, occs)
        refs = db.execute(
            "SELECT * FROM refs WHERE status='accepted' ORDER BY reference_id"
        ).fetchall()
        people = {r["person_id"] for r in refs}
        truths = []
        negatives = []
        for row in manifest("calibration"):
            who = row["people"][0]
            vec, reason = encode_query(row)
            if reason:
                raise ValueError("Unusable calibration probe " + row["path"])
            identity, score = rank_id(vec, refs)
            truths.append((who in people, identity == who, score))
            negatives.extend(
                c.dot(vec, r["embedding"])
                for r, true in indexed.values()
                if r["embedding"] is not None and true != who
            )
        absent = [s for enrolled, _, s in truths if not enrolled]
        genuine = [s for enrolled, correct, s in truths if enrolled and correct]
        if not genuine or not absent or not negatives:
            raise ValueError(
                "Calibration needs an eligible known and open-set trial plus stream distractors"
            )
        lower = max(c.BASELINE, max(absent) + 1e-6)
        keep = [s for s in genuine if s >= lower]
        cutoff = min(keep) if keep else lower
        config = dict(
            method="Highest cutoff retaining the maximum correct enrolled calibration trials "
            "with zero observed open-set false identifications; OpenCV baseline as floor.",
            model=c.MODEL,
            preprocessing=c.PRE,
            quality_policy=c.POLICY,
            gallery_hash=c.gallery_hash(refs),
            threshold=cutoff,
            search_threshold=max(c.BASELINE, max(negatives) + 1e-6),
            search_method="Maximum calibration distractor similarity plus epsilon, OpenCV baseline floor",
            calibration_known=len(truths) - len(absent),
            calibration_unenrolled=len(absent),
            owner_target_fpir=0.001,
            owner_target_gallery=1000,
            split_hashes=proof["split_hashes"],
        )
    path = BASE / "frozen-calibration.json"
    if path.exists() and json.loads(path.read_text()) != config:
        raise ValueError(
            "A different freeze already exists. Use a separate operator corpus root for a new experiment."
        )
    dump(path, config)
    active = c.gallery()["versions"]
    if not active or active[0]["calibration"] != config:
        c.publish(config)
    return config


def interval(success, n):
    """Two-sided 95% exact Clopper-Pearson interval for independent Bernoulli trials."""
    if not n:
        return None

    def invert(k, target):
        lo, hi = 0.0, 1.0
        for _ in range(55):
            p = (lo + hi) / 2
            cdf = sum(
                math.exp(
                    math.lgamma(n + 1)
                    - math.lgamma(j + 1)
                    - math.lgamma(n - j + 1)
                    + j * math.log(p)
                    + (n - j) * math.log1p(-p)
                )
                for j in range(k + 1)
            )
            if cdf > target:
                lo = p
            else:
                hi = p
        return (lo + hi) / 2

    return [
        0 if success == 0 else invert(success - 1, 0.975),
        1 if success == n else invert(success, 0.025),
    ]


def measure(extended=False):
    freeze_path = BASE / "frozen-calibration.json"
    if not freeze_path.exists():
        raise ValueError("Freeze thresholds before evaluating")
    config = json.loads(freeze_path.read_text())
    if audit(extended)["split_hashes"] != config["split_hashes"]:
        raise ValueError("Split changed after calibration")
    with c.connect() as db:
        attached(db)
        occs = select_occurrences(db, extended)
        indexed = rows(db, occs)
        active = next(
            v for v in reversed(c.gallery()["versions"]) if v["calibration"] == config
        )
        if active["calibration"] != config:
            raise ValueError("Active gallery differs from the frozen experiment")
        refs = db.execute(
            "SELECT * FROM refs JOIN membership USING(reference_id) WHERE version=?",
            (active["version"],),
        ).fetchall()
        enrolled = {r["person_id"] for r in refs}
        trials = []
        for row in manifest("evaluation"):
            who = row["people"][0]
            vec, reason = encode_query(row)
            predicted, score = rank_id(vec, refs)
            accepted = predicted if score >= config["threshold"] else None
            eligible = {oid for oid, occ in occs.items() if who in occ["people"]}
            ranked = (
                search(db, vec, config["search_threshold"], {"limit": 10}, list(occs))[
                    "groups"
                ]
                if vec
                else []
            )
            correct_at = []
            seen = set()
            for g in ranked:
                oid = g["occurrence_id"]
                labels = {indexed[o["detection_id"]][1] for o in g["observations"]}
                correct = oid in eligible and oid not in seen and labels == {who}
                correct_at.append(int(correct))
                if correct:
                    seen.add(oid)
            trials.append(
                dict(
                    subject=who,
                    session=row["sessions"][0],
                    enrolled=who in enrolled,
                    prediction=accepted,
                    score=score,
                    quality=reason,
                    eligible=len(eligible),
                    returned=len(ranked),
                    hits=correct_at,
                    eligible_assets=sorted(occs[oid]["asset_id"] for oid in eligible),
                    ranked_evidence=[
                        dict(
                            occurrence_id=g["occurrence_id"],
                            asset_id=g["asset_id"],
                            score=g["score"],
                            observation_ids=[
                                o["detection_id"] for o in g["observations"]
                            ],
                        )
                        for g in ranked
                    ],
                )
            )
        known = [r for r in trials if r["enrolled"]]
        unknown = [r for r in trials if not r["enrolled"]]
        fp = sum(r["prediction"] is not None for r in unknown)
        correct = sum(r["prediction"] == r["subject"] for r in known)
        positive = [r for r in trials if r["eligible"]]
        absent = [r for r in trials if not r["eligible"]]
        stats = {}
        for k in (1, 5, 10):
            stats[str(k)] = {
                "precision_at_k": sum(sum(r["hits"][:k]) / k for r in positive)
                / len(positive)
                if positive
                else None,
                "recall_at_k": sum(sum(r["hits"][:k]) / r["eligible"] for r in positive)
                / len(positive)
                if positive
                else None,
                "precision_returned": sum(sum(r["hits"][:k]) for r in positive)
                / max(1, sum(min(k, r["returned"]) for r in positive)),
            }
        frame_stats = Counter()
        detected = defaultdict(set)
        for obs, person in indexed.values():
            detected[obs["occurrence_id"], obs["frame_index"]].add(person)
        for oid, occ in occs.items():
            for fr in db.execute(
                "SELECT frame_index,face_count FROM feed.frames WHERE occurrence_id=?",
                (oid,),
            ):
                frame_stats["frames"] += 1
                if len(occ["people"]) == 2:
                    frame_stats["labeled_multiface_frames"] += 1
                    frame_stats["both_faces_detected_frames"] += int(
                        detected[oid, fr["frame_index"]] == set(occ["people"])
                        and fr["face_count"] == 2
                    )
                for condition, (first, last) in occ.get("conditions", {}).items():
                    if first <= fr["frame_index"] <= last:
                        frame_stats["labeled_" + condition + "_frames"] += 1
                        frame_stats[condition + "_frames_with_detection"] += int(
                            fr["face_count"] > 0
                        )
                if not occ["people"]:
                    frame_stats["labeled_empty_frames"] += 1
                    frame_stats["empty_frames_with_false_detections"] += int(
                        fr["face_count"] > 0
                    )
        strata = defaultdict(Counter)
        for obs, true_person in indexed.values():
            event = json.loads(obs["event_json"])
            le, re, nose = event["landmarks_xy"][:3]
            # A descriptive yaw proxy from geometry, independent of identity scores.
            mid = (le[0] + re[0]) / 2
            asset = occs[obs["occurrence_id"]]
            labels = [
                k
                for k, (lo, hi) in asset.get("conditions", {}).items()
                if lo <= obs["frame_index"] <= hi
            ]
            if len(asset["people"]) == 2:
                labels.append("multiple_faces")
            if abs(nose[0] - mid) / max(1, abs(le[0] - re[0])) > 0.30:
                frame_stats["pose_proxy_observations"] += 1
                labels.append("nonfrontal_proxy")
            for condition in labels:
                bucket = strata[condition]
                bucket["observations"] += 1
                bucket[obs["state"]] += 1
                who, score = rank_id(obs["embedding"], refs)
                if true_person in enrolled:
                    bucket["enrolled_observations"] += 1
                    bucket["correct_ids"] += int(
                        who == true_person and score >= config["threshold"]
                    )
                else:
                    bucket["unenrolled_observations"] += 1
                    bucket["false_ids"] += int(score >= config["threshold"])
        identity = {
            "gallery_people": len(enrolled),
            "correct_identifications": correct,
            "enrolled_probes": len(known),
            "correct_rate": correct / len(known) if known else None,
            "correct_rate_ci95": interval(correct, len(known)),
            "false_identifications": fp,
            "independent_unenrolled_probes": len(unknown),
            "fpir": fp / len(unknown) if unknown else None,
            "fpir_ci95": interval(fp, len(unknown)),
            "interval_method": "Clopper-Pearson, two-sided 95%",
            "target_fpir": 0.001,
            "target_gallery": 1000,
            "target_verified": False,
        }
        report = {
            "measured_utc": c.utc(time.time()),
            "gallery_version": active["version"],
            "threshold": config["threshold"],
            "search_threshold": config["search_threshold"],
            "identification": identity,
            "retrieval": stats,
            "present_probes": len(positive),
            "absent_probes": len(absent),
            "absent_returning_any": sum(r["returned"] > 0 for r in absent),
            "coverage": dict(frame_stats),
            "condition_observation_counts": {k: dict(v) for k, v in strata.items()},
            "searchable_observations": sum(
                r["embedding"] is not None for r, _ in indexed.values()
            ),
            "search_assets": {
                oid: {"asset_id": occ["asset_id"], "sha256": occ["sha256"]}
                for oid, occ in occs.items()
            },
            "search_corpus_occurrences": len(occs),
            "indexed_observations": len(indexed),
            "trials": trials,
        }
    dump(
        BASE / ("quality-extended.json" if extended else "quality-results.json"), report
    )
    return {k: v for k, v in report.items() if k not in ("trials", "search_assets")}


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=["audit", "freeze", "measure", "extended"])
    action = p.parse_args().action
    print(
        json.dumps(
            {
                "audit": audit,
                "freeze": freeze,
                "measure": measure,
                "extended": lambda: measure(True),
            }[action](),
            indent=2,
        )
    )
