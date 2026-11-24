"""Loopback-only operator API for archived media and frame evidence."""

import json
import os
import sqlite3
import secrets
import shutil
import time
from contextlib import closing
from pathlib import Path

from flask import Flask, abort, jsonify, request, send_file, send_from_directory
from werkzeug.exceptions import HTTPException
from waitress import serve

DATA = Path(os.environ.get("OPERATOR_DATA", "/data")).resolve()
DIST = Path(__file__).resolve().parent / "dist"
app = Flask(__name__, static_folder=None)
app.config.update(
    MAX_CONTENT_LENGTH=12 * 1024**2,
    TRUSTED_HOSTS=["localhost", "127.0.0.1", "[::1]", "operator-ui"],
)
OCC = """occurrence_id, source_id, asset_id, session_id, cycle, scheduled_at, started_at,
 finished_at, state, frame_count, duration_s, last_frame, synthetic"""


def feed():
    if not (DATA / "feed.sqlite").is_file():
        abort(503)
    db = sqlite3.connect(
        (DATA / "feed.sqlite").as_uri() + "?mode=ro", uri=True, timeout=3
    )
    db.row_factory = sqlite3.Row
    return db


def one(db, sql, args):
    row = db.execute(sql, args).fetchone()
    if row is None:
        abort(404)
    return dict(row)


def media_file(relative, mime):
    path = (DATA / relative).resolve()
    if not path.is_relative_to(DATA) or not path.is_file():
        abort(410)
    return send_file(path, mimetype=mime, conditional=True)


@app.before_request
def mutations():
    if (
        request.method in ("POST", "PUT", "PATCH", "DELETE")
        and request.headers.get("X-Visions-Action") != "operator"
    ):
        abort(403)

    if request.method == "POST" and request.path in (
        "/api/identity/gallery/publish",
        "/api/identity/gallery/reference",
    ):
        key = (DATA / ".gallery-admin").read_text().strip()
        if not secrets.compare_digest(
            request.headers.get("X-Gallery-Key", "").encode(), key.encode()
        ):
            abort(403)


@app.after_request
def headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    if request.path.startswith("/api/"):
        response.headers["Cache-Control"] = "private, no-store"
    return response


@app.errorhandler(HTTPException)
def http_error(error):
    messages = {
        400: "درخواست نامعتبر است.",
        403: "اجازهٔ انجام این کار را ندارید.",
        404: "این مورد پیدا نشد.",
        409: "درخواست با وضعیت فعلی سازگار نیست.",
        410: "مدت نگهداری این رسانه به پایان رسیده است.",
        413: "حجم فایل بیشتر از حد مجاز است.",
        422: "این تصویر قابل پردازش نیست.",
        503: "سرویس هنوز آماده نیست. دوباره تلاش کنید.",
    }
    return jsonify(error=messages.get(error.code, "درخواست انجام نشد.")), error.code


@app.errorhandler(Exception)
def server_error(error):
    app.logger.exception("Operator request failed")
    return jsonify(error="خطای فنی رخ داد. دوباره تلاش کنید."), 500


@app.get("/health")
def health():
    return {"ready": (DIST / "index.html").is_file()}


@app.get("/api/operator/recordings")
def recordings():
    before = request.args.get("before", 1e20, type=float)
    source = request.args.get("source", "")
    with closing(feed()) as db:
        where = "AND source_id=?" if source else ""
        args = (before, source) if source else (before,)
        items = [
            dict(r)
            for r in db.execute(
                f"""SELECT {OCC} FROM occurrences WHERE scheduled_at<?
              AND last_frame>=0 AND state!='expired' {where} ORDER BY scheduled_at DESC LIMIT 60""",
                args,
            )
        ]
        sources = [
            r[0]
            for r in db.execute("SELECT DISTINCT source_id FROM occurrences ORDER BY 1")
        ]
    return jsonify(items=items, sources=sources)


@app.get("/api/operator/recordings/<occ>")
def recording(occ):
    with closing(feed()) as db:
        return one(db, f"SELECT {OCC} FROM occurrences WHERE occurrence_id=?", (occ,))


@app.get("/api/operator/recordings/<occ>/media")
def video(occ):
    with closing(feed()) as db:
        item = one(
            db, "SELECT media_path,state FROM occurrences WHERE occurrence_id=?", (occ,)
        )
    if item["state"] == "expired":
        abort(410)
    return media_file(item["media_path"], "video/mp4")


@app.get("/api/operator/recordings/<occ>/frames")
def frames(occ):
    # A bounded timeline window supports arbitrary seeks on variable-rate footage.
    at = max(0, request.args.get("at", 0.0, type=float))
    target = request.args.get("frame", type=int)
    with closing(feed()) as db:
        if target is not None:
            at = one(
                db,
                "SELECT media_pts_s FROM frames WHERE occurrence_id=? AND frame_index=?",
                (occ, target),
            )["media_pts_s"]
        start = db.execute(
            """SELECT frame_index FROM frames WHERE occurrence_id=? AND media_pts_s<=?
                               ORDER BY frame_index DESC LIMIT 1""",
            (occ, max(0, at - 1)),
        ).fetchone()
        start = start[0] if start else 0
        items = {
            r["frame_index"]: dict(r, faces=[])
            for r in db.execute(
                """SELECT frame_index,pts,time_base_num,time_base_den,
                  media_pts_s,width,height,ingested_at FROM frames WHERE occurrence_id=? AND frame_index>=?
                  ORDER BY frame_index LIMIT 240""",
                (occ, start),
            )
        }
        if items:
            for row in db.execute(
                """SELECT detection_id,frame_index,event_json,topic,partition_id,kafka_offset,
                 latest_offset,sql_visible_at FROM observations WHERE occurrence_id=? AND frame_index BETWEEN ? AND ?
                 ORDER BY frame_index,detection_id""",
                (occ, start, max(items)),
            ):
                obs = dict(row)
                event = json.loads(obs.pop("event_json"))
                obs.update(box=event["bbox_xywh"], confidence=event["confidence"])
                items[row["frame_index"]]["faces"].append(obs)
        return jsonify(items=list(items.values()), at=at)


@app.get("/api/operator/crops/<identity>")
def crop(identity):
    with closing(feed()) as db:
        row = one(
            db, "SELECT crop_path FROM observations WHERE detection_id=?", (identity,)
        )
    return media_file(row["crop_path"], "image/png")


@app.get("/api/operator/original/<identifier>.png")
def original(identifier):
    from io import BytesIO
    import cv2

    with closing(feed()) as db:
        row = one(
            db,
            """SELECT o.frame_index, u.input_path FROM observations o
            JOIN occurrences u USING(occurrence_id) WHERE o.detection_id=?""",
            (identifier,),
        )
    path = (DATA / row["input_path"]).resolve()
    if not path.is_relative_to(DATA) or not path.is_file():
        abort(410)
    stream = cv2.VideoCapture(str(path), cv2.CAP_FFMPEG, [cv2.CAP_PROP_N_THREADS, 1])
    try:
        if row["frame_index"] and not stream.set(
            cv2.CAP_PROP_POS_FRAMES, row["frame_index"]
        ):
            abort(422)
        ok, frame = stream.read()
        if not ok:
            abort(422)
        ok, png = cv2.imencode(".png", frame)
        if not ok:
            abort(422)
    finally:
        stream.release()
    return send_file(BytesIO(png.tobytes()), mimetype="image/png")


@app.get("/api/operator/details/<identity>")
def details(identity):
    with closing(feed()) as db:
        row = one(
            db,
            "SELECT event_json,sql_row FROM observations WHERE detection_id=?",
            (identity,),
        )
    return jsonify(
        event=json.loads(row["event_json"]),
        sql=json.loads(row["sql_row"]) if row["sql_row"] else None,
    )


@app.get("/")
def index():
    return send_from_directory(DIST, "index.html")


@app.get("/assets/<path:name>")
def assets(name):
    return send_from_directory(DIST / "assets", name)


@app.get("/font/<path:name>")
def font(name):
    return send_from_directory(DIST / "font", name)


def create_app():
    DATA.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        fd = os.open(
            DATA / ".gallery-admin", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
        )
    except FileExistsError:
        pass
    else:
        with os.fdopen(fd, "w") as key:
            key.write(secrets.token_urlsafe(32))
    from identity.api import bp

    app.register_blueprint(bp)
    return app


if __name__ == "__main__":
    serve(create_app(), host="0.0.0.0", port=8092, threads=8)
