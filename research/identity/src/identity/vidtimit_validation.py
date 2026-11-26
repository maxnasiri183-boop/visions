"""Freeze thresholds on calibration identities; then score held-out identification and retrieval."""

from collections import Counter
import hashlib
import json
import os
from zipfile import ZipFile

import numpy as np

from . import core as c
from .corpus import get_image
from .evaluate import fingerprint, interval
from .import_vidtimit import digest
from .vidtimit_candidate import SIZES, frozen, original_frames


def feature(image):
    faces = c.detect(image)
    if len(faces) != 1:
        return None, f"Detected {len(faces)} faces"
    return c.features(image, faces[0])


def photograph_features(root, rows):
    probes = {}
    for row in rows:
        path = root / row["path"]
        if digest(path) != row["sha256"]:
            raise ValueError("Probe checksum changed")
        raw = path.read_bytes()
        vector, reason = feature(c.upload_image(raw))
        probes[row["person_id"]] = {"embedding": vector, "reason": reason}
    return probes


def audit_and_index(root, records):
    sessions = {role: {r["session_id"] for r in rows} for role, rows in records.items()}
    for role, group in sessions.items():
        if any(group & other for name, other in sessions.items() if name != role):
            raise ValueError("A recording session appears on both sides of the split")
    portraits = []
    for role in ("enrollment", "calibration", "evaluation"):
        for row in records[role]:
            image = c.upload_image((root / row["path"]).read_bytes())
            h, thumb = fingerprint(image)
            portraits.append((role, row["sha256"], h, thumb.astype(np.int16)))

    def leaks(role, sha, h, image):
        return sum(
            1
            for old, ref, hh, thumb in portraits
            if old != role
            and (
                sha == ref
                or (h ^ hh).bit_count() <= 6
                and np.abs(image.astype(np.int16) - thumb).mean() <= 2
            )
        )

    near = sum(leaks(*portrait) for portrait in portraits) // 2
    if near:
        raise ValueError("Potential cross-role portrait duplicate")
    vectors = []
    checked = 0
    sampled = 0
    failures = Counter()
    for row in records["stream"]:
        with ZipFile(root / "archives" / row["archive"]) as archive:
            names = original_frames(archive, row["person_id"], row["take"])
            # Three fixed positions per original take. Failed samples stay in the denominator.
            positions = {len(names) // 4, len(names) // 2, 3 * len(names) // 4}
            for i, name in enumerate(names):
                image = get_image(archive, name)
                h, thumb = fingerprint(image)
                if leaks(
                    "stream", hashlib.sha256(archive.read(name)).hexdigest(), h, thumb
                ):
                    raise ValueError(
                        "Potential shared frame across stream and portrait splits"
                    )
                checked += 1
                if i in positions:
                    sampled += 1
                    vector, reason = feature(image)
                    if reason:
                        failures[reason] += 1
                    else:
                        vectors.append((row["person_id"], vector))
    audit = {
        "disjoint_capture_sessions": True,
        "cross_role_exact_or_near_duplicates": 0,
        "checked_stream_frames": checked,
        "checked_photographs": len(portraits),
        "fixed_search_samples": sampled,
        "usable_search_vectors": len(vectors),
        "unusable_search_samples": dict(failures),
        "fingerprint": "All source frames vs all enrollment and probe images. DCT Hamming <=6 and 32px gray mean difference <=2/255.",
    }
    frozen(root / "split-audit.json", audit)
    return vectors, audit


def scores(vec, items):
    return (
        sorted(
            ((c.dot(vec, v), person) for person, v in items),
            key=lambda pair: (-pair[0], pair[1]),
        )
        if vec
        else []
    )


def operating_point(calibration, items):
    negatives = []
    for who, probe in calibration.items():
        negatives.extend(
            score
            for score, person in scores(probe["embedding"], items)
            if person != who
        )
    if not negatives:
        raise ValueError("Calibration yielded no usable impostor comparisons")
    return max(c.BASELINE, max(negatives) + 1e-6)


def trial(probes, items, targets, cutoff):
    counts = Counter()
    rows = []
    for who, probe in probes.items():
        present = who in targets
        counts["known" if present else "unknown"] += 1
        ranked = scores(probe["embedding"], items)
        best, winner = ranked[0] if ranked else (None, None)
        accepted = best is not None and best >= cutoff
        correct = accepted and winner == who
        if present:
            counts["correct_identifications"] += int(correct)
            counts["rank1_before_threshold"] += int(winner == who)
            counts["wrong_positive_known"] += int(accepted and not correct)
            counts["no_positive_known"] += int(not accepted)
        else:
            counts["false_identifications"] += int(accepted)
            counts["correct_no_hit"] += int(not accepted and bool(ranked))
        counts["failed_to_acquire"] += int(not ranked)
        rows.append(
            {
                "subject": who,
                "in_target": present,
                "winner": winner,
                "best_score": best,
                "accepted": accepted,
                "correct_positive": correct,
                "quality_failure": probe["reason"],
            }
        )
    known, unknown = counts["known"], counts["unknown"]
    fp, fn = counts["false_identifications"], known - counts["correct_identifications"]
    return {
        **dict(counts),
        "fpir": fp / unknown if unknown else None,
        "fpir_ci95": interval(fp, unknown),
        "fnir": fn / known if known else None,
        "fnir_ci95": interval(fn, known),
    }, rows


def evaluate(root, roles, records):
    with c.connect() as db:
        refs = db.execute(
            "SELECT * FROM refs WHERE status='accepted' ORDER BY person_id"
        ).fetchall()
    aliases = json.loads((root / "private-alias-crosswalk.json").read_text())
    truth = {value["person_id"]: who for who, value in aliases.items()}
    ids = {truth[r["person_id"]] for r in refs}
    if len(refs) != len(ids) or ids != {p for p, r in roles.items() if r["enrolled"]}:
        raise ValueError(
            "Candidate must have exactly one accepted portrait for each enrolled identity"
        )
    cal = photograph_features(root, records["calibration"])
    if set(cal) & {x["person_id"] for x in records["evaluation"]}:
        raise ValueError("Calibration and evaluation share subjects")
    stream_vec, audit = audit_and_index(root, records)
    present = {r["person_id"] for r in records["stream"]}
    if (
        present != {p for p, r in roles.items() if r["in_video"]}
        or {p for p, _ in stream_vec} != present
    ):
        raise ValueError(
            "Expected a quality-eligible search vector for every video subject"
        )
    search_threshold = operating_point(cal, stream_vec)
    order = sorted(
        refs,
        key=lambda r: hashlib.sha256(
            ("v43-size|" + truth[r["person_id"]]).encode()
        ).hexdigest(),
    )
    configs = {}
    for n in SIZES:
        subset = order[:n]
        items = [(truth[r["person_id"]], r["embedding"]) for r in subset]
        subset_ids = {p for p, _ in items}
        phase = {
            p: rec
            for p, rec in cal.items()
            if p in subset_ids or not roles[p]["enrolled"]
        }
        if not subset_ids & phase.keys():
            raise ValueError("Nested calibration lacks a known probe")
        configs[str(n)] = {
            "gallery_hash": c.gallery_hash(subset),
            "threshold": operating_point(phase, items),
            "calibration_known": len(subset_ids & phase.keys()),
            "calibration_unenrolled": len(set(phase) - subset_ids),
        }
    base = configs[str(SIZES[-1])]
    config = {
        **base,
        "method": "Maximum wrong-identity calibration cosine plus 1e-6 with OpenCV baseline floor",
        "model": c.MODEL,
        "preprocessing": c.PRE,
        "quality_policy": c.POLICY,
        "search_threshold": search_threshold,
        "search_method": "Maximum wrong-person score among fixed calibration queries to three session-2 frames per source take, with baseline floor",
        "manifest_protocol": "vidtimit43-v1",
        "alias_manifest_sha256": digest(root / "private-alias-crosswalk.json"),
        "split_hashes": {role: digest(root / (role + ".json")) for role in records},
        "owner_target_fpir": 0.001,
        "owner_target_gallery": 1000,
    }
    # Held-out features are created only after the gallery, both cutoffs and split plan have been written.
    frozen(root / "frozen-calibration.json", config)
    frozen(root / "frozen-gallery-sizes.json", configs)
    previous = c.gallery()["versions"]
    if previous:
        if previous[0]["calibration"] != config:
            raise ValueError("A different local version already exists")
    else:
        c.publish(config)
    held = photograph_features(root, records["evaluation"])
    comparison = {}
    evidence = {}
    for n in SIZES:
        items = [(truth[r["person_id"]], r["embedding"]) for r in order[:n]]
        subset = {p for p, _ in items}
        comparison[str(n)] = {}
        for phase, probes in [("calibration", cal), ("held_out", held)]:
            selected = {
                p: rec
                for p, rec in probes.items()
                if p in subset or not roles[p]["enrolled"]
            }
            stats, details = trial(
                selected, items, subset, configs[str(n)]["threshold"]
            )
            comparison[str(n)][phase] = stats
            evidence[f"identification-{n}-{phase}"] = details
    retrieval = {}
    for phase, probes in [("calibration", cal), ("held_out", held)]:
        stats, details = trial(probes, stream_vec, present, search_threshold)
        retrieval[phase] = {
            "present_queries": stats["known"],
            "absent_queries": stats["unknown"],
            "correct_thresholded_top1": stats.get("correct_identifications", 0),
            "absent_false_hits": stats.get("false_identifications", 0),
            "failed_to_acquire": stats["failed_to_acquire"],
            "absent_false_hit_rate": stats["fpir"],
            "absent_false_hit_ci95": stats["fpir_ci95"],
        }
        evidence["search-" + phase] = details
    frozen(root / "private-trials.json", evidence)
    held_id = {r["subject"]: r for r in evidence["identification-28-held_out"]}
    held_search = {r["subject"]: r for r in evidence["search-held_out"]}
    examples = {}
    for photo in records["evaluation"]:
        who = photo["person_id"]
        identification = held_id[who]
        query = held_search[who]
        if identification["quality_failure"] or query["quality_failure"]:
            continue
        if (
            roles[who]["enrolled"]
            and identification["correct_positive"]
            and query["correct_positive"]
        ):
            example = "enrolled_and_present"
        elif (
            not roles[who]["enrolled"]
            and not identification["accepted"]
            and query["correct_positive"]
        ):
            example = "unenrolled_but_present"
        elif (
            not roles[who]["in_video"]
            and not identification["accepted"]
            and not query["accepted"]
        ):
            example = "absent"
        else:
            continue
        if example not in examples:
            neutral_path = "example-photos/" + example + ".jpg"
            dest = root / neutral_path
            dest.parent.mkdir(mode=0o700, exist_ok=True)
            if dest.exists() and digest(dest) != photo["sha256"]:
                raise ValueError("An existing operator example has changed")
            if not dest.exists():
                os.link(root / photo["path"], dest)
            examples[example] = {
                "operator_photo": neutral_path,
                "photo": photo["path"],
                "source_session": photo["session_id"],
                "research_person": who,
                "gallery_alias": aliases[who]["display_name"]
                if roles[who]["enrolled"]
                else None,
                "gallery_score": identification["best_score"],
                "retrieval_score": query["best_score"],
                "matching_opaque_assets": [
                    "vt-" + x["sha256"][:24]
                    for x in records["stream"]
                    if x["person_id"] == who
                ],
            }
    frozen(root / "private-demo-examples.json", examples)
    report = {
        "release": "VidTIMIT 158963",
        "subjects": len(roles),
        "gallery_identities": len(refs),
        "gallery_reference_count": len(refs),
        "references_using_fallback_position": sum(
            not row["member"].endswith("/sa1/008") for row in records["enrollment"]
        ),
        "video_present": len(present),
        "video_absent": len(roles) - len(present),
        "original_clips": len(records["stream"]),
        "frames": audit["checked_stream_frames"],
        "duration_s": sum(r["frames"] / r["fps"] for r in records["stream"]),
        "source_fps": 25,
        "resolution_px": sorted(
            {f"{r['width']}x{r['height']}" for r in records["stream"]}
        ),
        "archives_bytes": sum(
            r["bytes"] for r in json.loads((root / "archive-ledger.json").read_text())
        ),
        "derived_media_bytes": sum(
            (root / r["path"]).stat().st_size for r in records["stream"]
        ),
        "nested_gallery_thresholds": {n: v["threshold"] for n, v in configs.items()},
        "identification_by_gallery_size": comparison,
        "search_threshold": search_threshold,
        "sampled_historical_search": retrieval,
        "audit": audit,
        "model": c.MODEL,
        "quality_policy": c.POLICY,
        "active_pilot_changed": False,
    }
    frozen(root / "quality-summary.json", report)
    return report
