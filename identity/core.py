"""Local SFace embeddings and immutable snapshots of gallery references."""

import contextlib
from datetime import datetime, timezone
from functools import lru_cache
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
import shutil
import sqlite3
import threading
import time
from uuid import uuid4

import cv2
import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

ROOT = Path(
    os.getenv("OPERATOR_ROOT", os.getenv("OPERATOR_DATA", "private/operator"))
).resolve()
MODEL_SHA = "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79"
YUNET_SHA = "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"
MODEL = "sface-2021dec-" + MODEL_SHA[:12]
PRE = "opencv4.11-alignCrop-5pt-112-L2-v1"
POLICY = "min-side30-eye8-luma15:245-laplacian8-v1"
BASELINE = 0.363
lock = threading.Lock()

DDL = """
CREATE TABLE IF NOT EXISTS refs (
 reference_id TEXT PRIMARY KEY, person_id TEXT NOT NULL, display_name TEXT NOT NULL,
 session_id TEXT NOT NULL, status TEXT NOT NULL, reason TEXT, portrait_path TEXT NOT NULL,
 sha256 TEXT NOT NULL, embedding BLOB, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS retired_draft_refs(reference_id TEXT PRIMARY KEY REFERENCES refs(reference_id));
CREATE VIEW IF NOT EXISTS eligible_draft AS SELECT r.* FROM refs r WHERE r.status='accepted'
 AND NOT EXISTS(SELECT 1 FROM retired_draft_refs h WHERE h.reference_id=r.reference_id);
CREATE TABLE IF NOT EXISTS versions (
 version INTEGER PRIMARY KEY, created_at REAL NOT NULL, threshold REAL NOT NULL,
 search_threshold REAL NOT NULL, model TEXT NOT NULL, preprocessing TEXT NOT NULL,
 policy TEXT NOT NULL, calibration TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS membership (
 version INTEGER NOT NULL REFERENCES versions(version),reference_id TEXT NOT NULL REFERENCES refs(reference_id),
 PRIMARY KEY(version,reference_id));
CREATE TABLE IF NOT EXISTS indexed (
 detection_id TEXT PRIMARY KEY,occurrence_id TEXT NOT NULL,frame_index INTEGER NOT NULL,source_id TEXT NOT NULL,
 ingested_at REAL NOT NULL,indexed_at REAL NOT NULL,media_pts_s REAL NOT NULL,crop_path TEXT NOT NULL,
 bbox TEXT NOT NULL,state TEXT NOT NULL,reason TEXT,embedding BLOB,align_ms REAL,embed_ms REAL,lookup_ms REAL);
CREATE INDEX IF NOT EXISTS by_time ON indexed(ingested_at);
CREATE INDEX IF NOT EXISTS by_camera ON indexed(source_id,ingested_at);
CREATE INDEX IF NOT EXISTS by_occurrence ON indexed(occurrence_id,frame_index);
CREATE TABLE IF NOT EXISTS decisions (
 detection_id TEXT PRIMARY KEY REFERENCES indexed(detection_id) ON DELETE CASCADE,version INTEGER,
 reference_id TEXT,state TEXT NOT NULL,score REAL,threshold REAL NOT NULL,reason TEXT);
CREATE TABLE IF NOT EXISTS reference_features (
 reference_id TEXT PRIMARY KEY REFERENCES refs(reference_id) ON DELETE CASCADE,model TEXT NOT NULL,
 preprocessing TEXT NOT NULL,quality_policy TEXT NOT NULL,bbox TEXT,face_path TEXT);
CREATE TABLE IF NOT EXISTS observation_features (
 detection_id TEXT PRIMARY KEY REFERENCES indexed(detection_id) ON DELETE CASCADE,model TEXT NOT NULL,
 preprocessing TEXT NOT NULL,quality_policy TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS probes (
 probe_id TEXT PRIMARY KEY,image_path TEXT NOT NULL,created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS probe_lineage (
 probe_id TEXT PRIMARY KEY REFERENCES probes(probe_id) ON DELETE CASCADE,model TEXT NOT NULL,
 preprocessing TEXT NOT NULL,quality_policy TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS probe_faces (
 probe_id TEXT REFERENCES probes(probe_id) ON DELETE CASCADE,face_index INTEGER,bbox TEXT,confidence REAL,
 thumbnail_path TEXT,embedding BLOB,reason TEXT,PRIMARY KEY(probe_id,face_index));
"""


def utc(stamp):
    return datetime.fromtimestamp(stamp, timezone.utc).isoformat()


@lru_cache(maxsize=1)
def init():
    ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    with contextlib.closing(
        sqlite3.connect(ROOT / "identity.sqlite", timeout=30)
    ) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript(DDL)
        # The original schema had one pinned algorithm. Name that algorithm literally during migration.
        legacy = (
            "sface-2021dec-0ba9fbfa01b5",
            "opencv4.11-alignCrop-5pt-112-L2-v1",
            "min-side30-eye8-luma15:245-laplacian8-v1",
        )
        with db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "CREATE TABLE IF NOT EXISTS identity_migrations(name TEXT PRIMARY KEY)"
            )
            if not db.execute(
                "SELECT 1 FROM identity_migrations WHERE name='legacy-lineage'"
            ).fetchone():
                db.execute(
                    "INSERT OR IGNORE INTO reference_features(reference_id,model,preprocessing,quality_policy) "
                    "SELECT reference_id,?,?,? FROM refs",
                    legacy,
                )
                db.execute(
                    "INSERT OR IGNORE INTO probe_lineage SELECT probe_id,?,?,? FROM probes",
                    legacy,
                )
                db.execute(
                    "INSERT OR IGNORE INTO observation_features SELECT detection_id,?,?,? FROM indexed",
                    legacy,
                )
                db.execute("INSERT INTO identity_migrations VALUES('legacy-lineage')")

        from .browse import initialize

        initialize(db)
        db.execute(
            "CREATE INDEX IF NOT EXISTS cards_person_scope ON gallery_cards(person_id,filter,scope DESC)"
        )


@contextlib.contextmanager
def connect():
    init()
    db = sqlite3.connect(ROOT / "identity.sqlite", timeout=20)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    try:
        with db:
            yield db
    finally:
        db.close()


def checked_path(relative):
    path = (ROOT / relative).resolve()
    if not path.is_relative_to(ROOT):
        raise ValueError("Invalid artifact path")
    return path


def upload_image(raw):
    if not raw or len(raw) > 8 * 1024 * 1024:
        raise ValueError("Supply a JPEG or PNG of at most 8 MiB")
    try:
        with Image.open(BytesIO(raw)) as image:
            if (
                image.format not in ("JPEG", "PNG")
                or image.width * image.height > 16_000_000
            ):
                raise ValueError("Supply a JPEG or PNG of at most 16 megapixels")
            return cv2.cvtColor(
                np.asarray(ImageOps.exif_transpose(image).convert("RGB")),
                cv2.COLOR_RGB2BGR,
            )
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError) as exc:
        raise ValueError("Image decoding failed") from exc


def save_image(relative, image):
    path = checked_path(relative)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if image.size == 0 or not cv2.imwrite(str(path), image):
        raise ValueError("Could not save image")
    return str(path.relative_to(ROOT))


@lru_cache(maxsize=1)
def engines():
    sface = ROOT / "models/sface.onnx"
    yunet = Path(os.getenv("IDENTITY_YUNET", str(ROOT.parent / "yunet.onnx")))
    for path, checksum in ((sface, MODEL_SHA), (yunet, YUNET_SHA)):
        if sha256(path.read_bytes()).hexdigest() != checksum:
            raise RuntimeError("Pinned identity model checksum mismatch")
    cv2.setNumThreads(1)
    return cv2.FaceDetectorYN.create(
        str(yunet), "", (320, 240), 0.85
    ), cv2.FaceRecognizerSF.create(str(sface), "")


def detect(image):
    h, w = image.shape[:2]
    scale = min(1.0, 1280 / max(w, h))
    small = (
        cv2.resize(image, (round(w * scale), round(h * scale))) if scale < 1 else image
    )
    with lock:
        detector, _ = engines()
        detector.setInputSize((small.shape[1], small.shape[0]))
        _, rows = detector.detect(small)
    if rows is None:
        return []
    rows[:, 0:14:2] *= w / small.shape[1]
    rows[:, 1:14:2] *= h / small.shape[0]
    return sorted(rows, key=lambda row: float(row[0]))


def crop(image, bbox):
    x, y, w, h = bbox
    return image[
        max(0, int(y)) : max(0, int(np.ceil(y + h))),
        max(0, int(x)) : max(0, int(np.ceil(x + w))),
    ]


def features(image, face, timings=None):
    chip = crop(image, face[:4])
    if (
        chip.size == 0
        or min(chip.shape[:2]) < 30
        or np.linalg.norm(face[4:6] - face[6:8]) < 8
    ):
        return None, "Face or eye separation too small"
    gray = cv2.cvtColor(chip, cv2.COLOR_BGR2GRAY)
    if not 15 <= gray.mean() <= 245:
        return None, "Face exposure outside quality bounds"
    if cv2.Laplacian(gray, cv2.CV_64F).var() < 8:
        return None, "Face lacks local detail"
    with lock:
        _, model = engines()
        start = time.monotonic()
        aligned = model.alignCrop(image, face)
        middle = time.monotonic()
        vec = model.feature(aligned).flatten()
        if timings is not None:
            timings.update(
                align_ms=(middle - start) * 1000,
                embed_ms=(time.monotonic() - middle) * 1000,
            )
    norm = np.linalg.norm(vec)
    if vec.size != 128 or not np.isfinite(norm) or norm < 1e-6:
        raise ValueError("Invalid SFace output")
    return (vec / norm).astype("<f4").tobytes(), None


def add_reference(raw, person, name, session):
    if not all(
        isinstance(v, str) and 0 < len(v.strip()) <= 120
        for v in (person, name, session)
    ):
        raise ValueError(
            "A person ID, display name and capture session are required (max 120 characters)"
        )
    image = upload_image(raw)
    rows = detect(image)
    vec, reason = (
        features(image, rows[0])
        if len(rows) == 1
        else (None, f"Expected one face, detected {len(rows)}")
    )
    digest = sha256(raw).hexdigest()
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        existing = db.execute(
            "SELECT * FROM refs JOIN reference_features USING(reference_id) WHERE sha256=? AND person_id=? AND session_id=?",
            (digest, person, session),
        ).fetchone()
        if existing:
            return public_ref(existing)
        if db.execute("SELECT 1 FROM refs WHERE sha256=?", (digest,)).fetchone():
            raise ValueError("This image already belongs to another person or session")
        ref = uuid4().hex
        path = save_image("references/" + ref + ".jpg", image)
        db.execute(
            "INSERT INTO refs VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                ref,
                person,
                name,
                session,
                "rejected" if reason else "accepted",
                reason,
                path,
                digest,
                vec,
                time.time(),
            ),
        )
        bbox = [float(v) for v in rows[0][:4]] if len(rows) == 1 else None
        face_path = (
            save_image("references/" + ref + "-face.jpg", crop(image, bbox))
            if bbox
            else None
        )
        db.execute(
            "INSERT INTO reference_features VALUES(?,?,?,?,?,?)",
            (ref, MODEL, PRE, POLICY, json.dumps(bbox), face_path),
        )
        return public_ref(
            db.execute(
                "SELECT * FROM refs JOIN reference_features USING(reference_id) WHERE reference_id=?",
                (ref,),
            ).fetchone()
        )


def public_ref(row):
    return {
        **{
            k: row[k]
            for k in (
                "reference_id",
                "person_id",
                "display_name",
                "session_id",
                "model",
                "preprocessing",
                "quality_policy",
                "status",
                "reason",
            )
        },
        "face_url": "/api/identity/references/" + row["reference_id"] + "/face"
        if row["status"] == "accepted"
        else None,
        "portrait_url": "/api/identity/references/" + row["reference_id"] + "/portrait",
    }


def gallery_hash(refs):
    return sha256(
        json.dumps(sorted((r["person_id"], r["sha256"]) for r in refs)).encode()
    ).hexdigest()


def publish(calibration=None):
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        refs = db.execute(
            "SELECT reference_id,person_id,sha256 FROM eligible_draft"
        ).fetchall()
        incompatible = db.execute(
            """SELECT 1 FROM eligible_draft r JOIN reference_features f USING(reference_id)
               WHERE (f.model!=? OR f.preprocessing!=? OR f.quality_policy!=?) LIMIT 1""",
            (MODEL, PRE, POLICY),
        ).fetchone()
        if incompatible:
            raise ValueError(
                "Draft gallery contains references from a different model or preprocessing version"
            )
        if not refs:
            raise ValueError("Add at least one eligible reference before publishing")
        if calibration and (
            calibration["gallery_hash"] != gallery_hash(refs)
            or calibration["model"] != MODEL
            or calibration["preprocessing"] != PRE
        ):
            raise ValueError("Calibration and draft gallery differ")
        calibration = calibration or {
            "method": "Uncalibrated OpenCV LFW operating point",
            "threshold": BASELINE,
            "search_threshold": BASELINE,
        }
        cur = db.execute(
            "INSERT INTO versions(created_at,threshold,search_threshold,model,preprocessing,policy,calibration) VALUES(?,?,?,?,?,?,?)",
            (
                time.time(),
                calibration["threshold"],
                calibration["search_threshold"],
                MODEL,
                PRE,
                POLICY,
                json.dumps(calibration),
            ),
        )
        version = cur.lastrowid
        db.executemany(
            "INSERT INTO membership VALUES(?,?)",
            [(version, r["reference_id"]) for r in refs],
        )
    from .browse import versions

    with connect() as db:
        return next(v for v in versions(db, version) if v["version"] == version)


def gallery(version=None):
    with connect() as db:
        versions = [
            dict(r)
            for r in db.execute("""SELECT v.*,COUNT(m.reference_id) reference_count,
            COUNT(DISTINCT r.person_id) people_count FROM versions v JOIN membership m USING(version)
            JOIN refs r USING(reference_id) GROUP BY v.version ORDER BY v.version DESC""")
        ]
        for item in versions:
            item["created_at"] = utc(item["created_at"])
            item["calibration"] = json.loads(item["calibration"])
        if version is None:
            refs = db.execute(
                "SELECT * FROM refs JOIN reference_features USING(reference_id) WHERE NOT EXISTS(SELECT 1 FROM retired_draft_refs h WHERE h.reference_id=refs.reference_id) ORDER BY person_id,created_at"
            )
        else:
            if version not in [v["version"] for v in versions]:
                raise KeyError("Gallery version not found")
            refs = db.execute(
                "SELECT * FROM refs JOIN reference_features USING(reference_id) JOIN membership USING(reference_id) WHERE version=? ORDER BY person_id",
                (version,),
            )
        return dict(
            active_version=versions[0]["version"] if versions else None,
            versions=versions,
            references=[public_ref(r) for r in refs],
        )


def dot(a, b):
    return float(np.clip(np.frombuffer(a, "<f4") @ np.frombuffer(b, "<f4"), -1.0, 1.0))


def reference_face(ref):
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT r.*,f.face_path,f.bbox FROM refs r JOIN reference_features f USING(reference_id) "
            "WHERE reference_id=? AND status='accepted'",
            (ref,),
        ).fetchone()
        if row is None:
            raise KeyError("Eligible reference not found")
        if row["face_path"] and checked_path(row["face_path"]).is_file():
            return row["face_path"]
        # Backfill earlier enrollments from the retained portrait and check the original vector.
        img = upload_image(checked_path(row["portrait_path"]).read_bytes())
        if row["bbox"]:
            box = json.loads(row["bbox"])
        else:
            faces = detect(img)
            if len(faces) != 1:
                raise ValueError("Original reference face could not be recovered")
            vec, reason = features(img, faces[0])
            if reason or dot(vec, row["embedding"]) < 0.98:
                raise ValueError("Reference face differs from enrollment")
            box = [float(v) for v in faces[0][:4]]
        path = save_image("references/" + ref + "-face.jpg", crop(img, box))
        db.execute(
            "UPDATE reference_features SET face_path=?,bbox=? WHERE reference_id=?",
            (path, json.dumps(box), ref),
        )
        return path


def expire_probes(db, keep):
    stale = db.execute(
        """SELECT probe_id FROM probes WHERE created_at<? OR probe_id IN
            (SELECT probe_id FROM probes ORDER BY created_at DESC LIMIT -1 OFFSET ?)""",
        (time.time() - 86400, keep),
    ).fetchall()
    for row in stale:
        db.execute("DELETE FROM probes WHERE probe_id=?", (row[0],))
        shutil.rmtree(checked_path("probes/" + row[0]), ignore_errors=True)
