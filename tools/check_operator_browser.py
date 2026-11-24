"""Exercise Persian operator journeys with an authorized private pilot and Chromium."""

import argparse
from contextlib import closing
from io import BytesIO
import json
import os
from pathlib import Path
import sqlite3
from urllib.parse import parse_qs, urlencode, urlparse

from PIL import Image
from playwright.sync_api import expect, sync_playwright


def address(base, row):
    return (
        base
        + "/?"
        + urlencode(dict(view="recordings", occ=row[0], frame=row[1], face=row[2]))
    )


def check_box(page, geometry):
    error = page.locator(".box.active").evaluate(
        """(box, [x,y,w,h]) => {
      const v=document.querySelector('video'), r=v.getBoundingClientRect(), b=box.getBoundingClientRect();
      const scale=Math.min(r.width/v.videoWidth,r.height/v.videoHeight);
      const left=r.left+(r.width-scale*v.videoWidth)/2, top=r.top+(r.height-scale*v.videoHeight)/2;
      return Math.max(Math.abs(left+x*scale-b.left),Math.abs(top+y*scale-b.top),
                      Math.abs(w*scale-b.width),Math.abs(h*scale-b.height));
    }""",
        geometry,
    )
    assert error < 1.5, f"Overlay differs from fitted video geometry by {error:.2f}px"
    return round(error, 2)


def exercise(data, base, browser_path):
    pins = (
        json.loads((data / "pinned.json").read_text())
        if (data / "pinned.json").exists()
        else []
    )
    with closing(
        sqlite3.connect((data / "feed.sqlite").as_uri() + "?mode=ro", uri=True)
    ) as db:
        db.execute(
            "ATTACH DATABASE ? AS identity",
            ((data / "identity.sqlite").as_uri() + "?mode=ro",),
        )
        # Prefer a pinned calibrated two-person recording when available.
        candidates = db.execute("""SELECT o.occurrence_id,o.frame_index,o.detection_id,o.event_json,d.version
            FROM observations o JOIN occurrences u USING(occurrence_id) JOIN decisions d USING(detection_id)
            WHERE u.asset_id='duo' AND u.state='complete' AND d.state='matched' AND EXISTS (
            SELECT 1 FROM observations other JOIN decisions od USING(detection_id)
            WHERE other.occurrence_id=o.occurrence_id AND other.frame_index=o.frame_index AND od.state='unknown')
            ORDER BY d.version DESC,o.frame_index,u.started_at DESC""").fetchall()
        selected = next((row for row in candidates if row[0] in pins), candidates[0])
        other = db.execute(
            """SELECT o.detection_id FROM observations o JOIN decisions d USING(detection_id)
             WHERE o.occurrence_id=? AND o.frame_index=? AND d.state='unknown'""",
            selected[:2],
        ).fetchone()[0]
        poor = db.execute("""SELECT o.occurrence_id,o.frame_index,o.detection_id FROM observations o JOIN decisions d
             USING(detection_id) JOIN occurrences u USING(occurrence_id) WHERE d.state='poor_quality'
             AND u.state='complete' ORDER BY u.started_at DESC LIMIT 1""").fetchone()
        empty = db.execute(
            "SELECT occurrence_id FROM occurrences WHERE asset_id='empty' AND state='complete' ORDER BY started_at DESC LIMIT 1"
        ).fetchone()[0]
    screen_dir = data / "screens"
    screen_dir.mkdir(exist_ok=True)
    box = json.loads(selected[3])["bbox_xywh"]
    summary = {"max_overlay_error_px": 0.0, "gallery_version": selected[4]}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=browser_path)
        page = browser.new_page(viewport={"width": 1365, "height": 900})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        for width in [1365, 390]:
            page.set_viewport_size({"width": width, "height": 900})
            page.goto(address(base, selected))
            expect(page.get_by_text("هویت پیشنهادی", exact=True)).to_be_visible()
            expect(page.locator(".box")).to_have_count(2)
            page.wait_for_function(
                "Array.from(document.querySelectorAll('.pair img')).every(i=>i.naturalWidth>0)"
            )
            assert page.evaluate("document.body.scrollWidth") <= width
            for mode in ["window", "fullscreen"]:
                if mode == "fullscreen":
                    page.get_by_role("button", name="تمام‌صفحه").click()
                    page.wait_for_function("!!document.fullscreenElement")
                    page.wait_for_function(
                        "Math.abs(document.querySelector('.overlay').offsetWidth-Math.min(innerWidth,innerHeight*document.querySelector('video').videoWidth/document.querySelector('video').videoHeight))<2"
                    )
                summary["max_overlay_error_px"] = max(
                    summary["max_overlay_error_px"], check_box(page, box)
                )
                if mode == "fullscreen":
                    page.evaluate("document.exitFullscreen()")
            page.screenshot(
                path=str(screen_dir / f"verified-review-{width}.png"), full_page=True
            )
            page.get_by_text("جزئیات فنی", exact=True).click()
            expect(page.locator("pre")).to_contain_text(selected[2])
            tech = json.loads(page.locator("pre").inner_text())
            assert tech["sql"]
            assert (
                page.request.get(
                    base + "/api/operator/original/" + selected[2] + ".png"
                )
                .body()
                .startswith(b"\x89PNG")
            )
            pts = page.locator("video").evaluate("(v)=>v.currentTime")
            page.locator(".box:not(.active)").focus()
            page.keyboard.press("Enter")
            expect(page.get_by_text("ناشناخته", exact=True)).to_be_visible()
            assert parse_qs(urlparse(page.url).query)["face"] == [other]
            assert (
                abs(page.locator("video").evaluate("(v)=>v.currentTime") - pts) < 0.002
            )
            page.locator(".box:not(.active)").click()
            page.get_by_role("button", name="پروندهٔ مرجع").click()
            page.wait_for_function(
                "document.querySelector('.portrait-grid img')?.naturalWidth>0"
            )
            page.get_by_role("button", name="بازگشت به صفحهٔ قبل").click()
            expect(page.locator(".box.active")).to_be_visible()
            assert "face=" + selected[2] in page.url
            page.get_by_role("button", name="پخش", exact=True).click()
            page.wait_for_function(
                "!document.querySelector('video').paused && document.querySelector('video').currentTime>0.1"
            )
            page.get_by_role("button", name="توقف", exact=True).click()
            page.get_by_role("button", name="فریم بعدی").click()
            assert page.locator("video").evaluate("(v)=>v.paused")
        page.goto(address(base, poor))
        expect(page.get_by_text("کیفیت تصویر کافی نیست", exact=True)).to_be_visible()
        page.goto(base + "/?view=recordings&occ=" + empty + "&frame=0")
        expect(page.get_by_text("این فریم پردازش شد", exact=False)).to_be_visible()
        for subject, decision in [
            ("fadg0", "ناشناخته"),
            ("faks0", "هویت پیشنهادی"),
            ("fcmr0", None),
        ]:
            page.goto(base + "/?view=search")
            page.locator("input[type=file]").set_input_files(
                data / "research/upload" / (subject + ".jpg")
            )
            with page.expect_response(
                lambda r: r.url.endswith("/api/identity/search")
            ) as event:
                page.get_by_role("button", name="جست‌وجوی حضورها").click()
            result = event.value.json()
            if not decision:
                assert not result["groups"]
                expect(
                    page.get_by_text("در این بازه حضوری", exact=False)
                ).to_be_visible()
                continue
            first = result["groups"][0]
            best = max(first["observations"], key=lambda r: r["score"])
            page.screenshot(
                path=str(screen_dir / f"verified-search-{subject}.png"), full_page=True
            )
            page.locator(".hit").first.get_by_role(
                "button", name="باز کردن لحظهٔ ویدیو"
            ).click()
            expect(page.locator(".box.active")).to_be_visible()
            query = parse_qs(urlparse(page.url).query)
            assert query["face"] == [best["detection_id"]] and query["occ"] == [
                first["occurrence_id"]
            ]
            assert query["frame"] == [str(best["frame_index"])]
            assert (
                abs(
                    page.locator("video").evaluate("(v)=>v.currentTime")
                    - best["media_pts_s"]
                )
                < 0.002
            )
            # Retrieval and gallery decisions have separate thresholds.
            stored = page.request.get(
                base + "/api/identity/observations/" + best["detection_id"]
            ).json()
            expect(
                page.get_by_text(
                    {"matched": "هویت پیشنهادی", "unknown": "ناشناخته"}[
                        stored["state"]
                    ],
                    exact=True,
                )
            ).to_be_visible()
        page.goto(base + "/?view=search")
        page.locator("input[type=file]").set_input_files(
            data / "research/examples/multi-probe.jpg"
        )
        expect(page.locator(".picker button")).to_have_count(2)
        page.locator(".picker button").nth(1).click()
        page.locator('input[type="datetime-local"]').first.fill("2099-01-01T00:00")
        page.get_by_role("button", name="جست‌وجوی حضورها").click()
        expect(page.get_by_text("در این بازه حضوری", exact=False)).to_be_visible()
        buf = BytesIO()
        Image.new("RGB", (180, 130)).save(buf, "PNG")
        page.locator("input[type=file]").set_input_files(
            dict(name="empty.png", mimeType="image/png", buffer=buf.getvalue())
        )
        expect(page.get_by_text("چهرهٔ قابل استفاده‌ای", exact=False)).to_be_visible()
        page.goto(base + "/?view=live")
        expect(page.locator("video")).to_be_visible()
        page.wait_for_function("document.querySelector('video').currentTime>0")
        page.goto(base + "/?view=health")
        expect(page.locator(".health-grid h3").first).to_be_visible()
        assert not errors, errors
        browser.close()
    print(json.dumps(summary))


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data", type=Path, default=Path("private/operator"))
    p.add_argument("--url", default="http://127.0.0.1:8092")
    p.add_argument("--chrome", default=os.environ.get("CHROME_BIN"))
    a = p.parse_args()
    exercise(a.data.resolve(), a.url.rstrip("/"), a.chrome)
