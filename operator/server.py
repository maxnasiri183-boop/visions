"""Loopback-only operator API for archived media and frame evidence."""

import json
import os
import secrets
from pathlib import Path

from flask import Flask, abort, jsonify, request, send_from_directory
from werkzeug.exceptions import HTTPException
from waitress import serve

DATA = Path(os.environ.get("OPERATOR_DATA", "/data")).resolve()
DIST = Path(__file__).resolve().parent / "dist"
app = Flask(__name__, static_folder=None)
app.config.update(
    MAX_CONTENT_LENGTH=12 * 1024**2,
    TRUSTED_HOSTS=["localhost", "127.0.0.1", "[::1]", "operator-ui"],
)
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
