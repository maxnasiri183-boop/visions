"""Read stored decisions and manage a private enrollment/search workflow."""

import json
import sqlite3
import shutil
import time
from uuid import uuid4

from flask import Blueprint, abort, jsonify, request, send_file

from . import core as c
from . import browse
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
    # Legacy clients retain the original shape for small galleries.
    with c.connect() as db:
        check_version(db, version)
        versions = browse.versions(db, version)
        join = (
            "JOIN membership m USING(reference_id) WHERE m.version=?"
            if version
            else "WHERE NOT EXISTS(SELECT 1 FROM retired_draft_refs h WHERE h.reference_id=r.reference_id)"
        )
        refs = db.execute(
            f"""SELECT r.*,f.face_path,f.model,f.preprocessing,f.quality_policy FROM refs r
            JOIN reference_features f USING(reference_id) {join} ORDER BY r.person_id,r.reference_id LIMIT 49""",
            (version,) if version else (),
        ).fetchall()
        return jsonify(
            versions=versions,
            active_version=versions[0]["version"] if versions else None,
            references=[c.public_ref(r) for r in refs[:48]],
            truncated=len(refs) > 48,
        )


def check_version(db, version):
    if (
        version is not None
        and not db.execute(
            "SELECT 1 FROM versions WHERE version=?", (version,)
        ).fetchone()
    ):
        abort(404)


def scope(db):
    raw = request.args.get("version")
    version = int(raw) if raw else None
    check_version(db, version)
    return version or 0


@bp.get("/versions")
def version_list():
    with c.connect() as db:
        rows = browse.versions(db, scope(db))
        person = request.args.get("person")
        historical = (
            db.execute(
                "SELECT scope FROM gallery_cards WHERE person_id=? AND filter='' AND scope>0 ORDER BY scope DESC LIMIT 1",
                (person,),
            ).fetchone()
            if person
            else None
        )
        return jsonify(
            versions=rows,
            active_version=rows[0]["version"] if rows else None,
            person_version=historical[0] if historical else None,
        )


@bp.get("/people")
def people():
    with c.connect() as db:
        return jsonify(
            browse.people(
                db,
                scope(db),
                request.args.get("q", ""),
                request.args.get("status", ""),
                request.args.get("after", ""),
                int(request.args.get("limit", 24)),
            )
        )


@bp.get("/people/<person>")
def person_record(person):
    with c.connect() as db:
        return jsonify(
            browse.references(db, scope(db), person, request.args.get("after", ""))
        )


@bp.get("/references/<ref>/face-thumbnail")
@bp.get("/references/<ref>/thumbnail")
def portrait_thumbnail(ref):
    return image_response(browse.thumbnail(ref))


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
    # Preserve the frozen policy if the operator republishes unchanged enrollment.
    with c.connect() as db:
        previous = db.execute(
            "SELECT calibration FROM versions ORDER BY version DESC LIMIT 1"
        ).fetchone()
        eligible = c.gallery_hash(
            db.execute("SELECT person_id,sha256 FROM eligible_draft")
        )
    candidates = [json.loads(previous[0])] if previous else []
    path = c.ROOT / "research" / "frozen-calibration.json"
    if path.exists():
        candidates.append(json.loads(path.read_text()))
    calibration = next(
        (
            item
            for item in candidates
            if item.get("gallery_hash") == eligible
            and item.get("model") == c.MODEL
            and item.get("preprocessing") == c.PRE
        ),
        None,
    )
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
    return reference_face(ref)


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
        "/api/identity/references/" + data["reference_id"] + "/face"
        if data["reference_id"]
        else None
    )
    data["face_url"] = (
        "/api/identity/references/" + data["reference_id"] + "/face"
        if data["reference_id"]
        else None
    )
    return jsonify(data)


@bp.post("/observations/<identifier>/compare")
def compare_saved_pair(identifier):
    """Recompute one cosine against the eligible reference used for this historical decision."""
    with c.connect() as db:
        pair = db.execute(
            """SELECT i.embedding probe,r.embedding portrait,r.reference_id,d.version,d.threshold,
          f.model,f.preprocessing FROM indexed i JOIN decisions d USING(detection_id)
          JOIN membership m ON m.reference_id=d.reference_id AND m.version=d.version
          JOIN refs r ON r.reference_id=m.reference_id JOIN observation_features f USING(detection_id)
          JOIN reference_features rf ON rf.reference_id=r.reference_id AND rf.model=f.model
          AND rf.preprocessing=f.preprocessing WHERE i.detection_id=? AND d.state='matched'
          AND i.embedding IS NOT NULL AND r.embedding IS NOT NULL""",
            (identifier,),
        ).fetchone()
    if pair is None:
        abort(409)
    score = c.dot(pair["probe"], pair["portrait"])
    return jsonify(
        detection_id=identifier,
        reference_id=pair["reference_id"],
        version=pair["version"],
        model=pair["model"],
        preprocessing=pair["preprocessing"],
        threshold=pair["threshold"],
        score=score,
        above_threshold=score >= pair["threshold"],
        computed_at=time.time(),
        method="on_demand_pair_cosine",
    )


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
