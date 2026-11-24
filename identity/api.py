"""Read stored decisions and manage a private enrollment/search workflow."""

import json

from flask import Blueprint, abort, jsonify, request, send_file

from . import core as c

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


