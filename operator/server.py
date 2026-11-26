"""Loopback-only operator API for archived media and frame evidence."""

import json
import os
import sqlite3
import secrets
import shutil
import time
from contextlib import closing
from pathlib import Path
from urllib.request import ProxyHandler, build_opener
from urllib.error import URLError

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
    if os.environ.get("GALLERY_FIXTURE") == "1" and request.path.startswith("/api/"):
        allowed = (
            "/api/identity/versions",
            "/api/identity/people",
            "/api/identity/references/",
        )
        if request.method != "GET" or not request.path.startswith(allowed):
            abort(403)
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
        thumbnail = request.path.startswith(
            "/api/identity/references/"
        ) and request.path.endswith("/thumbnail")
        response.headers["Cache-Control"] = (
            "private, max-age=86400"
            if thumbnail and response.status_code in (200, 304)
            else "private, no-store"
        )
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
    ready = (DIST / "index.html").is_file()
    return jsonify(
        ready=ready, revision=os.getenv("VISIONS_REVISION", "unrecorded")
    ), 200 if ready else 503


@app.route("/api/operator/sources/<source>/speed", methods=["GET", "POST"])
def source_speed(source):
    # Changes apply when the planner assigns a new occurrence. In-flight retries keep their original timing.
    catalog = json.loads((DATA / "catalog.json").read_text())
    if source not in {entry["source_id"] for entry in catalog}:
        abort(404)
    choices = [0.5, 1.0, 2.0]
    path = DATA / "feed.sqlite"
    with closing(
        sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, timeout=4)
    ) as db:
        if not db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='replay_controls'"
        ).fetchone():
            abort(503)
        if request.method == "POST":
            body = request.get_json(silent=True)
            value = body.get("speed") if isinstance(body, dict) else None
            if type(value) not in (int, float) or value not in choices:
                abort(400)
            with db:
                db.execute(
                    """INSERT INTO replay_controls VALUES(?,?,?) ON CONFLICT(source_id)
                           DO UPDATE SET speed=excluded.speed,changed_at=excluded.changed_at""",
                    (source, value, time.time()),
                )
        wanted = db.execute(
            "SELECT speed FROM replay_controls WHERE source_id=?", (source,)
        ).fetchone()
        active = db.execute(
            """SELECT speed FROM occurrences WHERE source_id=? AND state='processing'
                             ORDER BY scheduled_at DESC LIMIT 1""",
            (source,),
        ).fetchone()
        last = db.execute(
            "SELECT speed FROM occurrences WHERE source_id=? ORDER BY scheduled_at DESC LIMIT 1",
            (source,),
        ).fetchone()
    return jsonify(
        source_id=source,
        configured=wanted[0] if wanted else (last[0] if last else 1.0),
        current=active[0] if active else None,
        choices=choices,
    )


@app.get("/api/operator/recordings")
def recordings():
    before = request.args.get("before", 1e20, type=float)
    source = request.args.get("source", "")
    with closing(feed()) as db:
        live_run = os.environ.get("OPERATOR_LIVE_RUN") if request.args.get("live") == "1" else None
        if live_run:
            sources = [r[0] for r in db.execute("SELECT DISTINCT source_id FROM occurrences WHERE run_id=? ORDER BY 1",(live_run,))]
            camera = source or (sources[0] if sources else "")
            now = time.time()
            # Allow time for segment closure, GPU detection and identity lookup.
            delay = 8
            rows = db.execute(f"""SELECT {OCC} FROM occurrences WHERE source_id=? AND run_id=?
                AND state NOT IN ('expired','expiring') AND scheduled_at<=? ORDER BY scheduled_at DESC LIMIT 1""",
                (camera,live_run,now-delay))
            pulse = db.execute("SELECT state,heartbeat_at FROM service_state WHERE service=?",("capture:"+camera,)).fetchone()
            status = pulse[0] if pulse and now-pulse[1]<12 else "disconnected"
            return jsonify(items=[dict(r) for r in rows],sources=sources,camera=camera,
                           connection=status,server_time=now,delay=delay)
        where = "AND source_id=?" if source else ""
        args = (before, source) if source else (before,)
        items = [
            dict(r)
            for r in db.execute(
                f"""SELECT {OCC} FROM occurrences WHERE scheduled_at<?
              AND (last_frame>=0 OR session_id LIKE 'rtsp-%') AND state NOT IN ('expired','expiring') {where} ORDER BY scheduled_at DESC LIMIT 60""",
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
        elif request.args.get("live") == "1":
            # Read the committed tail in source time, including variable-rate recordings.
            latest = db.execute(
                "SELECT media_pts_s FROM frames WHERE occurrence_id=? ORDER BY frame_index DESC LIMIT 1",
                (occ,),
            ).fetchone()
            at = max(0, latest[0] - 2) if latest else 0
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
            ident = DATA / "identity.sqlite"
            decision_join = ""
            fields = "NULL AS identity_state,NULL AS identity_name"
            if ident.is_file():
                db.execute("ATTACH DATABASE ? AS ident", (ident.as_uri()+"?mode=ro",))
                decision_join = (" LEFT JOIN ident.decisions d USING(detection_id) "
                                 "LEFT JOIN ident.refs r ON d.reference_id=r.reference_id ")
                fields = "d.state AS identity_state,CASE WHEN d.state='matched' THEN r.display_name END AS identity_name"
            for row in db.execute(
                f"""SELECT detection_id,frame_index,event_json,topic,partition_id,kafka_offset,
                 latest_offset,sql_visible_at,{fields} FROM observations {decision_join}
                 WHERE occurrence_id=? AND frame_index BETWEEN ? AND ?
                 ORDER BY frame_index,detection_id""",
                (occ, start, max(items)),
            ):
                obs = dict(row)
                event = json.loads(obs.pop("event_json"))
                obs.update(box=event["bbox_xywh"], confidence=event["confidence"])
                items[row["frame_index"]]["faces"].append(obs)
        return jsonify(items=list(items.values()), at=at)


@app.get("/api/operator/detections")
def detections():
    camera = request.args.get("source", "")
    verdict = request.args.get("state", "")
    if verdict not in ("", "pending", "matched", "unknown", "poor_quality", "error"):
        abort(400)
    cursor = request.args.get("before", 2**63-1, type=int)
    if not 0 <= cursor <= 2**63-1:
        abort(400)
    run = os.getenv("OPERATOR_LIVE_RUN", "")
    with closing(feed()) as db:
        identity = DATA / "identity.sqlite"
        joins = ""
        status, name, version, score = "'pending'", "NULL", "NULL", "NULL"
        if identity.is_file():
            db.execute("ATTACH DATABASE ? AS ident", (identity.as_uri()+"?mode=ro",))
            joins = "LEFT JOIN ident.decisions d USING(detection_id) LEFT JOIN ident.refs r USING(reference_id)"
            status, name = "coalesce(d.state,'pending')", "CASE WHEN d.state='matched' THEN r.display_name END"
            version, score = "d.version", "d.score"
        rows = db.execute(f"""SELECT b.rowid AS cursor,b.detection_id,b.occurrence_id,b.frame_index,o.source_id,
                o.scheduled_at+f.media_pts_s/o.speed AS observed_at,{status} AS state,
                {name} AS name,{version} AS version,{score} AS score
            FROM observations b JOIN occurrences o USING(occurrence_id)
            JOIN frames f USING(occurrence_id,frame_index) {joins}
            WHERE b.rowid<? AND o.state NOT IN ('expired','expiring') AND (?='' OR o.run_id=?)
            AND (?='' OR o.source_id=?) AND (?='' OR {status}=?)
            ORDER BY b.rowid DESC LIMIT 49""",
            (cursor,run,run,camera,camera,verdict,verdict)).fetchall()
        sources = [r[0] for r in db.execute("SELECT DISTINCT source_id FROM occurrences WHERE (?='' OR run_id=?) ORDER BY 1",(run,run))]
        return jsonify(items=[dict(r) for r in rows[:48]],sources=sources,
                       next_cursor=rows[47]["cursor"] if len(rows)>48 else None)


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


@app.get("/api/operator/soak")
def soak_report():
    with closing(feed()) as db:
        run = db.execute(
            "SELECT run_id FROM runs ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    if not run:
        return jsonify(outcome="unavailable")
    directory = (DATA / "soaks" / run["run_id"]).resolve()
    if not directory.is_relative_to(DATA / "soaks"):
        abort(404)
    for name in ("report.json", "status.json"):
        path = directory / name
        if path.is_file():
            return send_file(path, mimetype="application/json", conditional=False)
    return jsonify(outcome="unavailable")


@app.get("/api/operator/routine-load")
def routine_load():
    # English engineering drill-down for Grafana. Only return Routine Load diagnostics.
    try:
        with build_opener(ProxyHandler({})).open(
            "http://operator-exporter:9125/summary", timeout=2
        ) as response:
            summary = json.load(response)
    except (URLError, TimeoutError, OSError, ValueError):
        return jsonify(
            available=False,
            state="unavailable",
            last_error="Operator exporter unavailable",
        ), 503
    return jsonify(observed_at=summary.get("observed_at"), **summary["routine_load"])


@app.get("/api/operator/health")
def source_health():
    now = time.time()
    with closing(feed()) as db:
        statuses = {
            r["service"]: dict(r) for r in db.execute("SELECT * FROM service_state")
        }
        sources = {k.split(":", 1)[1] for k in statuses if k.startswith("source:")}
        sources.update(
            r[0] for r in db.execute("SELECT DISTINCT source_id FROM occurrences")
        )
        identity = (DATA / "identity.sqlite").is_file()
        if identity:
            db.execute(
                "ATTACH DATABASE ? AS identities",
                ((DATA / "identity.sqlite").as_uri() + "?mode=ro",),
            )
        has_receipts = db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='source_received'"
        ).fetchone()
        items = []
        for source in sorted(sources):
            receipt = (
                db.execute(
                    "SELECT frame_index, ingested_at, observed_at, lag_ms FROM source_received WHERE source_id=?",
                    (source,),
                ).fetchone()
                if has_receipts
                else None
            )
            last = db.execute(
                """SELECT f.frame_index,f.ingested_at,f.indexed_at,f.face_count,f.media_pts_s,f.occurrence_id
                 FROM occurrences u JOIN frames f USING(occurrence_id) WHERE u.source_id=?
                 ORDER BY u.scheduled_at DESC, f.frame_index DESC LIMIT 1""",
                (source,),
            ).fetchone()
            queued = db.execute(
                """SELECT count(*) n, min(scheduled_at) oldest FROM occurrences
                 WHERE source_id=? AND state='queued'""",
                (source,),
            ).fetchone()
            indexed = (
                db.execute(
                    """SELECT max(indexed_at) AS last FROM identities.indexed
                     WHERE source_id=? AND state='ready'""",
                    (source,),
                ).fetchone()[0]
                if identity
                else None
            )
            errs = (
                db.execute(
                    """SELECT count(*) FROM identities.indexed WHERE source_id=?
                     AND state='error' AND indexed_at>?""",
                    (source, now - 300),
                ).fetchone()[0]
                if identity
                else None
            )
            items.append(
                dict(
                    source_id=source,
                    source=statuses.get("source:" + source),
                    queue=statuses.get("queue:" + source),
                    received=dict(receipt) if receipt else None,
                    frame=dict(last) if last else None,
                    searchable_at=indexed,
                    recognition_errors_5m=errs,
                    pending_clips=queued["n"],
                    backlog_age_s=max(0, now - queued["oldest"])
                    if queued["oldest"]
                    else 0,
                )
            )
        notices = [
            dict(r)
            for r in db.execute("""SELECT at,service,state FROM notices
                  WHERE state IN ('retry','source_outage','storage_guard','backpressure','expired') ORDER BY at DESC LIMIT 12""")
        ]
        recoveries = [
            dict(r)
            for r in db.execute("SELECT * FROM recoveries ORDER BY at DESC LIMIT 8")
        ]
        expired = db.execute(
            "SELECT coalesce(sum(expired),0) FROM run_totals"
        ).fetchone()[0]
    return jsonify(
        at=now,
        items=items,
        feeder=statuses.get("feeder"),
        worker=statuses.get("worker"),
        notices=notices,
        recoveries=recoveries,
        expired=expired,
        free_bytes=shutil.disk_usage(DATA).free,
    )


def examples():
    manifest = DATA / "research/upload.json"
    rows = json.loads(manifest.read_text()) if manifest.is_file() else []
    if (DATA / "research/examples/multi-probe.jpg").is_file():
        rows.append(
            dict(path="research/examples/multi-probe.jpg", gallery_label="composed")
        )
    return rows


@app.get("/api/operator/examples")
def example_index():
    return jsonify(
        [
            {
                "label": "نمونهٔ "
                + str(i + 1)
                + " · "
                + {
                    "enrolled": "عضو گالری",
                    "present": "خارج از گالری",
                    "absent": "بدون سابقه",
                    "composed": "دو چهره · تصویر ترکیبی",
                }.get(row.get("gallery_label"), "عکس آزمایشی"),
                "url": f"/api/operator/examples/{i}",
            }
            for i, row in enumerate(examples())
        ]
    )


@app.get("/api/operator/examples/<int:index>")
def example_image(index):
    rows = examples()
    if index >= len(rows):
        abort(404)
    return media_file(rows[index]["path"], "image/jpeg")


@app.get("/")
def index():
    if os.environ.get("GALLERY_FIXTURE") == "1":
        html = (
            (DIST / "index.html")
            .read_text()
            .replace("<html ", '<html data-gallery-fixture="true" ', 1)
        )
        return app.response_class(html, mimetype="text/html")
    if os.getenv("IDENTITY_FIRST_PILOT") == "1":
        html = (DIST / "index.html").read_text().replace(
            "<html ", '<html data-identity-pilot="1" ', 1
        )
        return app.response_class(html, mimetype="text/html")
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
    from identity.sighting_api import bp as sighting_bp
    app.register_blueprint(sighting_bp)
    return app


if __name__ == "__main__":
    serve(create_app(), host="0.0.0.0", port=8092, threads=8)
