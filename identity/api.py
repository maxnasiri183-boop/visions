"""Read stored decisions and manage a private enrollment/search workflow."""

import json
import sqlite3
import shutil
import time
from uuid import uuid4

from flask import Blueprint, abort, jsonify, request, send_file

from . import core as c
from .search import search

bp = Blueprint("identity", __name__, url_prefix="/api/identity")


@bp.errorhandler(ValueError)
def bad_input(error):
    return jsonify(error=str(error)), 400


@bp.errorhandler(KeyError)
def missing(error):
    return jsonify(error=error.args[0]), 404


def file_bytes():
    file = request.files.get("file")
    if file is None:
        raise ValueError("Choose a JPEG or PNG file")
    return file.stream.read(8 * 1024 * 1024 + 1)


@bp.get("/gallery")
@bp.get("/gallery/<int:version>")
def gallery(version=None):
    return jsonify(c.gallery(version))


@bp.post("/gallery/reference")
def reference():
    return jsonify(
        c.add_reference(
            file_bytes(),
            request.form.get("person_id"),
            request.form.get("display_name"),
            request.form.get("session_id"),
        )
    ), 201


@bp.post("/gallery/publish")
def publish():
    # Reuse a calibration only for the identical eligible membership.
    calibration = None
    path = c.ROOT / "research" / "frozen-calibration.json"
    if path.exists():
        candidate = json.loads(path.read_text())
        with c.connect() as db:
            refs = db.execute("SELECT * FROM refs WHERE status='accepted'").fetchall()
            if c.gallery_hash(refs) == candidate["gallery_hash"]:
                calibration = candidate
    return jsonify(c.publish(calibration)), 201


def image_response(path):
    response = send_file(c.checked_path(path), conditional=True)
    response.headers["Cache-Control"] = "private, no-store"
    return response


@bp.get("/references/<ref>/appearances")
def appearances(ref):
    from .appearances import for_reference
    return jsonify(for_reference(ref, request.args.get("version", type=int)))


@bp.get("/references/<ref>/portrait")
def portrait(ref):
    with c.connect() as db:
        row = db.execute(
            "SELECT portrait_path FROM refs WHERE reference_id=?", (ref,)
        ).fetchone()
    if not row:
        abort(404)
    return image_response(row[0])


@bp.get("/references/<ref>/face")
def reference_face(ref):
    return image_response(c.reference_face(ref))


@bp.get("/observations/<identifier>")
def observation(identifier):
    with c.connect() as db:
        row = db.execute(
            """SELECT d.*,r.person_id,r.display_name,COALESCE(f.model,v.model) model,
            COALESCE(f.preprocessing,v.preprocessing) preprocessing,COALESCE(f.quality_policy,v.policy) quality_policy
            FROM decisions d LEFT JOIN refs r USING(reference_id) LEFT JOIN observation_features f USING(detection_id)
            LEFT JOIN versions v ON v.version=d.version
                             WHERE d.detection_id=?""",
            (identifier,),
        ).fetchone()
    if not row:
        return jsonify(
            state="pending",
            detection_id=identifier,
            reason="Awaiting recognition worker",
        )
    data = dict(row)
    if data["state"] != "matched":
        data.update(
            candidate_person_id=data["person_id"],
            candidate_display_name=data["display_name"],
            person_id=None,
            display_name=None,
        )
    data["portrait_url"] = (
        "/api/identity/references/" + data["reference_id"] + "/portrait"
        if data["reference_id"]
        else None
    )
    data["face_url"] = (
        "/api/identity/references/" + data["reference_id"] + "/face"
        if data["reference_id"]
        else None
    )
    return jsonify(data)


@bp.get("/observations/<identifier>/thumbnail")
def thumbnail(identifier):
    with c.connect() as db:
        row = db.execute(
            "SELECT crop_path FROM indexed WHERE detection_id=?", (identifier,)
        ).fetchone()
    if not row:
        abort(404)
    return image_response(row[0])


@bp.post("/probes")
def probes():
    image = c.upload_image(file_bytes())
    rows = c.detect(image)
    identifier = uuid4().hex
    directory = "probes/" + identifier + "/"
    faces, records = [], []
    try:
        path = c.save_image(directory + "query.jpg", image)
        for i, row in enumerate(rows):
            vec, reason = c.features(image, row)
            box = [float(x) for x in row[:4]]
            chip = c.save_image(directory + str(i) + ".jpg", c.crop(image, box))
            records.append(
                (identifier, i, json.dumps(box), float(row[-1]), chip, vec, reason)
            )
            faces.append(
                dict(
                    face_index=i,
                    bbox_xywh=box,
                    confidence=float(row[-1]),
                    reason=reason,
                    thumbnail_url=f"/api/identity/probes/{identifier}/faces/{i}",
                )
            )
        with c.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            c.expire_probes(db, 99)
            db.execute(
                "INSERT INTO probes VALUES(?,?,?)", (identifier, path, time.time())
            )
            db.execute(
                "INSERT INTO probe_lineage VALUES(?,?,?,?)",
                (identifier, c.MODEL, c.PRE, c.POLICY),
            )
            db.executemany("INSERT INTO probe_faces VALUES(?,?,?,?,?,?,?)", records)
    except Exception:
        shutil.rmtree(c.checked_path(directory), ignore_errors=True)
        raise
    return jsonify(
        probe_id=identifier,
        image_url=f"/api/identity/probes/{identifier}/image",
        width=image.shape[1],
        height=image.shape[0],
        faces=faces,
    ), 201


@bp.get("/probes/<identifier>/image")
@bp.get("/probes/<identifier>/faces/<int:index>")
def probe_image(identifier, index=None):
    with c.connect() as db:
        if index is None:
            row = db.execute(
                "SELECT image_path FROM probes WHERE probe_id=?", (identifier,)
            ).fetchone()
        else:
            row = db.execute(
                "SELECT thumbnail_path FROM probe_faces WHERE probe_id=? AND face_index=?",
                (identifier, index),
            ).fetchone()
    if not row:
        abort(404)
    return image_response(row[0])


@bp.post("/search")
def query():
    body = request.get_json()
    if not isinstance(body, dict):
        raise ValueError("Supply an image face or detection ID")
    with c.connect() as db:
        if body.get("detection_id"):
            row = db.execute(
                "SELECT i.embedding,i.reason FROM indexed i JOIN observation_features f USING(detection_id) WHERE detection_id=? AND f.model=? AND f.preprocessing=?",
                (body["detection_id"], c.MODEL, c.PRE),
            ).fetchone()
        else:
            row = db.execute(
                "SELECT embedding,reason FROM probe_faces JOIN probe_lineage USING(probe_id) WHERE probe_id=? AND face_index=? AND model=? AND preprocessing=?",
                (body.get("probe_id"), body.get("face_index"), c.MODEL, c.PRE),
            ).fetchone()
        if not row:
            raise KeyError("Selected face is unavailable")
        if not row["embedding"]:
            raise ValueError(row["reason"] or "No eligible face embedding")
        version = db.execute(
            "SELECT version,search_threshold FROM versions WHERE model=? AND preprocessing=? ORDER BY version DESC LIMIT 1",
            (c.MODEL, c.PRE),
        ).fetchone()
        output = search(
            db,
            row["embedding"],
            version["search_threshold"] if version else c.BASELINE,
            body,
        )
        return jsonify(**output, version=version["version"] if version else None)


@bp.get("/status")
def status():
    with c.connect() as db:
        counts = {
            r[0]: r[1]
            for r in db.execute("SELECT state,count(*) FROM indexed GROUP BY state")
        }
        last = db.execute("SELECT MAX(indexed_at) FROM indexed").fetchone()[0]
        backlog = None
        if (c.ROOT / "feed.sqlite").is_file():
            db.execute("ATTACH DATABASE ? AS feed", (str(c.ROOT / "feed.sqlite"),))
            try:
                backlog = db.execute(
                    "SELECT count(*) FROM feed.observations o WHERE NOT EXISTS(SELECT 1 FROM indexed i WHERE i.detection_id=o.detection_id)"
                ).fetchone()[0]
            except sqlite3.OperationalError:
                pass
    return jsonify(
        counts=counts,
        backlog=backlog,
        latest_indexed_at=last,
        model=c.MODEL,
        preprocessing=c.PRE,
    )
