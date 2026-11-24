"""Exercise the video-first workspace in Chromium using real retained pilot evidence.

Run: CHROME_BIN=/path/to/chrome python tools/check_video_workspace.py --record
Playwright's FFmpeg is required for --record (python -m playwright install ffmpeg).
The race and HTTP 503 happen only inside the test tab.
"""

import argparse
import asyncio
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sqlite3
from urllib.parse import parse_qs, urlencode, urlparse

from playwright.async_api import Error, expect, async_playwright


VIEWPORTS = [(1920, 1080), (1440, 900), (1280, 800), (820, 1180), (390, 844)]
COMPARE = "مقایسه با گالری"
APPEARANCES = "مشاهده‌های دیگر"
RETURN = "بازگشت به لحظه قبلی"


@contextmanager
def database(root):
    def uri(name):
        return (root / name).as_uri() + "?mode=ro"

    with closing(sqlite3.connect(uri("feed.sqlite"), uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute("ATTACH DATABASE ? AS identity", (uri("identity.sqlite"),))
        yield db


OBSERVATION = """
SELECT o.detection_id,o.occurrence_id,o.frame_index,o.crop_path,o.event_json,
       f.media_pts_s,d.state,d.version,d.score,d.reference_id,d.threshold,
       r.person_id,r.display_name,r.sha256 AS reference_sha256,u.asset_id
FROM observations o JOIN decisions d USING(detection_id)
JOIN occurrences u USING(occurrence_id) JOIN frames f USING(occurrence_id,frame_index)
LEFT JOIN refs r USING(reference_id)
"""


def observation(db, identifier):
    row = db.execute(OBSERVATION + " WHERE o.detection_id=?", (identifier,)).fetchone()
    assert row, f"No retained observation {identifier}"
    result = dict(row)
    result["event"] = json.loads(result.pop("event_json"))
    return result


def fixtures(root):
    pins = json.loads((root / "pinned.json").read_text())
    assert pins, "Expected the authorized pilot's retained demo occurrences"
    params = ",".join("?" for _ in pins)
    with database(root) as db:
        known_row = db.execute(
            OBSERVATION
            + f""" WHERE o.occurrence_id IN ({params}) AND u.asset_id='duo'
            AND u.state='complete' AND d.state='matched' AND o.frame_index>0
            AND o.sql_row IS NOT NULL AND EXISTS (
              SELECT 1 FROM observations b JOIN decisions other USING(detection_id)
              WHERE b.occurrence_id=o.occurrence_id AND b.frame_index=o.frame_index
              AND other.state='unknown')
            ORDER BY d.version DESC,o.frame_index LIMIT 1""",
            pins,
        ).fetchone()
        assert known_row, "Pinned two-person recording lacks matched + unknown evidence"
        known = observation(db, known_row["detection_id"])
        other = db.execute(
            OBSERVATION
            + """ WHERE o.occurrence_id=? AND o.frame_index=? AND d.state='unknown'""",
            (known["occurrence_id"], known["frame_index"]),
        ).fetchone()
        unknown = observation(db, other["detection_id"])
        empty = db.execute(
            f"""SELECT f.occurrence_id, f.frame_index, f.media_pts_s FROM frames f JOIN occurrences u
            USING(occurrence_id) WHERE f.occurrence_id IN ({params}) AND u.asset_id='empty'
            AND u.state='complete' AND f.face_count=0 ORDER BY f.frame_index LIMIT 1""",
            pins,
        ).fetchone()
        assert empty, "Pinned no-face recording missing"
        poor = db.execute(
            OBSERVATION
            + f""" WHERE o.occurrence_id IN ({params}) AND d.state='poor_quality'
            AND u.asset_id='quality-stress' AND u.state='complete' LIMIT 1""",
            pins,
        ).fetchone()
        assert poor, "Pinned quality-gate example missing"
        for row in (known, unknown):
            assert (
                sha256((root / row["crop_path"]).read_bytes()).hexdigest()
                == row["event"]["crop_sha256"]
            )
        return known, unknown, dict(empty), observation(db, poor["detection_id"])


def address(base, row, select=True):
    query = dict(view="recordings", occ=row["occurrence_id"], frame=row["frame_index"])
    if select:
        query["face"] = row["detection_id"]
    return base + "/?" + urlencode(query)


def button(page, label):
    return page.get_by_role("button", name=label, exact=True)


def box(page, row):
    return page.locator(f'.box[data-detection-id="{row["detection_id"]}"]')


def check_query(url, row):
    q = parse_qs(urlparse(url).query)
    assert q.get("occ") == [row["occurrence_id"]], (q, row["occurrence_id"])
    assert q.get("frame") == [str(row["frame_index"])], (q, row["frame_index"])
    assert q.get("face") == [row["detection_id"]], (q, row["detection_id"])


async def moment(page, row):
    check_query(page.url, row)
    await expect(box(page, row)).to_have_class(re.compile(r"\bactive\b"))
    await page.wait_for_function(
        """t => { const v=document.querySelector('.stage video');
        return v && v.paused && !v.seeking && Math.abs(v.currentTime-t)<0.003 }""",
        arg=row["media_pts_s"],
    )


async def decision(page, row):
    await expect(
        page.locator(f'.inspector [data-decision="{row["state"]}"]')
    ).to_be_visible()
    await moment(page, row)
    panel = page.locator(".inspector")
    expected = ["/api/operator/crops/" + row["detection_id"]]
    if row["state"] == "matched":
        expected.append("/api/identity/references/" + row["reference_id"] + "/face")
    images = panel.locator(".pair img")
    await expect(images).to_have_count(len(expected))
    for path in expected:
        image = panel.locator(f'.pair img[src="{path}"]')
        await expect(image).to_be_visible()
        await expect(image).to_have_js_property("complete", True)
        assert await image.evaluate("i => i.naturalWidth > 0"), f"Broken image {path}"


async def alignment(page, row):
    return await box(page, row).evaluate(
        """(b, xywh) => {
          const v=document.querySelector('.stage video'), r=v.getBoundingClientRect();
          const k=Math.min(r.width/v.videoWidth,r.height/v.videoHeight), g=b.getBoundingClientRect();
          const actual=[g.left,g.top,g.width,g.height];
          const want=[r.left+(r.width-v.videoWidth*k)/2+xywh[0]*k,
                      r.top+(r.height-v.videoHeight*k)/2+xywh[1]*k,xywh[2]*k,xywh[3]*k];
          return Math.max(...actual.map((n,i)=>Math.abs(n-want[i])));
        }""",
        row["event"]["bbox_xywh"],
    )


async def within_viewport(page):
    metrics = await page.evaluate("""() => ({
      width:document.documentElement.clientWidth, content:document.documentElement.scrollWidth,
      direction:getComputedStyle(document.body).direction, language:document.documentElement.lang,
      reducedMotion:matchMedia('(prefers-reduced-motion: reduce)').matches
    })""")
    assert metrics["content"] <= metrics["width"] + 1, metrics
    assert metrics["direction"] == "rtl" and metrics["language"].startswith("fa"), (
        metrics
    )
    assert metrics["reducedMotion"], metrics


async def capture(page, out, name):
    await page.evaluate("document.fonts.ready")
    if await page.locator('html[lang="fa"]').count():
        assert await page.evaluate(
            "Array.from(document.fonts).some(f=>f.family==='Vazirmatn' && f.status==='loaded')"
        )
    if await page.locator(".stage video").count():
        await page.wait_for_function("""() => {const v=document.querySelector('.stage video');
          return v.readyState>=2 && v.videoWidth>0 && !v.seeking}""")
        await expect(page.locator(".vt-film img").first).to_be_attached(timeout=12000)
        await page.wait_for_function("""() => Array.from(document.querySelectorAll('.vt-film img, .pair img'))
          .every(img=>img.complete && img.naturalWidth>0)""")
    await page.screenshot(path=out / (name + ".png"), animations="disabled")
    if page.video:
        # Give the viewer time to read this verified state in the optional recording.
        await page.wait_for_timeout(950)


async def desktop_case(page, base, root, out, known, unknown, empty, poor, report):
    await page.goto(address(base, known, False))
    await expect(page.locator(".box")).to_have_count(2)
    if page.video:
        await capture(page, out, "two-faces-before-selection")
    await box(page, known).click()
    await decision(page, known)
    await expect(page.locator(".inspector h2").first).to_have_text("شناسایی چهره")
    with_known = page.locator(".inspector")
    endpoint = "/api/identity/observations/" + known["detection_id"]
    async with page.expect_response(lambda r: urlparse(r.url).path == endpoint) as evt:
        await button(page, COMPARE).click()
    saved = await (await evt.value).json()
    assert saved["state"] == "matched" and saved["version"] == known["version"]
    assert (
        saved["reference_id"] == known["reference_id"]
        and saved["score"] == known["score"]
    )
    await decision(page, known)
    ui_score = await page.evaluate(
        "x=>x.toLocaleString('fa-IR',{maximumFractionDigits:3})", known["score"]
    )
    await expect(with_known.locator(".facts").first).to_contain_text(ui_score)
    crop = await page.request.get(base + "/api/operator/crops/" + known["detection_id"])
    assert (
        crop.ok
        and sha256(await crop.body()).hexdigest() == known["event"]["crop_sha256"]
    )
    await capture(page, out, "known-comparison-1440")
    await with_known.get_by_text("جزئیات فنی", exact=True).click()
    evidence = with_known.locator("pre")
    await expect(evidence).to_contain_text(known["detection_id"])
    assert (await with_known.locator("code").text_content()) == known["detection_id"]
    original = await page.request.get(
        base + "/api/operator/original/" + known["detection_id"] + ".png"
    )
    assert original.ok and (await original.body()).startswith(b"\x89PNG")
    payload = json.loads(await evidence.inner_text())
    assert payload["event"]["bbox_xywh"] == known["event"]["bbox_xywh"]
    assert payload["sql"]["detection_id"] == known["detection_id"]
    assert await evidence.evaluate("e=>getComputedStyle(e).direction") == "ltr"
    await with_known.get_by_text("جزئیات فنی", exact=True).click()
    await button(page, APPEARANCES).click()
    hits = page.locator(".appearances button[data-detection-id]")
    await expect(hits.first).to_be_visible(timeout=30000)
    await within_viewport(page)
    await hits.first.scroll_into_view_if_needed()
    await page.wait_for_function(
        """() => { const strip=document.querySelector('.track-scroll').getBoundingClientRect();
          const visible=Array.from(document.querySelectorAll('.appearances img')).filter(i=>{
            const r=i.getBoundingClientRect();return r.left<Math.min(innerWidth,strip.right)
              && r.right>Math.max(0,strip.left);
          }); return visible.length>0 && visible.every(i=>i.complete&&i.naturalWidth>0); }"""
    )
    await capture(page, out, "other-appearances-track")
    ids = await hits.evaluate_all("buttons=>buttons.map(b=>b.dataset.detectionId)")
    with database(root) as db:
        # Rolling retention may expire a lower-ranked airing after search. Select a currently retained result.
        placeholders = ",".join("?" for _ in ids)
        retained = {
            r[0]
            for r in db.execute(
                f"SELECT detection_id FROM observations WHERE detection_id IN ({placeholders})",
                ids,
            )
        }
        options = [
            observation(db, key)
            for key in ids
            if key in retained and key != known["detection_id"]
        ]
    assert options, "No retained appearance distinct from the selected observation"
    hit = max(
        options,
        key=lambda o: (
            o["asset_id"] != known["asset_id"],
            o["occurrence_id"] != known["occurrence_id"],
        ),
    )
    await page.locator(
        f'.appearances button[data-detection-id="{hit["detection_id"]}"]'
    ).click()
    await moment(page, hit)
    await decision(page, hit)
    await capture(page, out, "other-appearance")
    await page.reload()
    await decision(page, hit)
    await button(page, RETURN).click()
    await decision(page, known)
    await capture(page, out, "original-moment-restored")
    await page.reload()
    await decision(page, known)
    await box(page, unknown).focus()
    await page.keyboard.press("Enter")
    await decision(page, unknown)
    await expect(with_known.get_by_text("ناشناخته", exact=True)).to_be_visible()
    await capture(page, out, "unknown")
    # A normal archive click reuses the app instance; stale-face bugs can disappear on a page reload.
    archive = page.locator(".record-archive")
    await archive.locator("summary").click()
    empty_row = (
        archive.locator(".record-row")
        .filter(has=page.get_by_text("empty", exact=True))
        .first
    )
    if await empty_row.count():
        await empty_row.click()
    else:
        # The small archive page moves while the larger playlist is playing.
        # Navigate to the pinned empty clip through the running SPA router without reloading React.
        await page.evaluate(
            """url => { history.pushState({visions:true},'',url);
            window.dispatchEvent(new PopStateEvent('popstate')); }""",
            address(base, empty, False),
        )
    await expect(page.get_by_text(re.compile("چهره‌ای|بدون چهره")).first).to_be_visible()
    await expect(page.locator(".inspector .pair img")).to_have_count(0)
    assert "face" not in parse_qs(urlparse(page.url).query)
    await page.goto(address(base, empty, False))
    await expect(page.locator(".stage video")).to_be_visible()
    await expect(page.get_by_text(re.compile("چهره‌ای|بدون چهره")).first).to_be_visible()
    await expect(page.locator(".box")).to_have_count(0)
    await expect(page.locator(".inspector .pair img")).to_have_count(0)
    await capture(page, out, "zero-faces")
    await page.goto(address(base, poor))
    await decision(page, poor)
    report["known"] = {
        k: known[k]
        for k in (
            "occurrence_id",
            "frame_index",
            "media_pts_s",
            "detection_id",
            "version",
            "score",
        )
    }
    report["other_appearance"] = {
        k: hit[k]
        for k in (
            "occurrence_id",
            "asset_id",
            "frame_index",
            "media_pts_s",
            "detection_id",
            "state",
        )
    }


async def faults(page, base, known, unknown, out):
    target = re.compile(
        re.escape(base + "/api/identity/observations/" + known["detection_id"])
        + r"(?:\?.*)?$"
    )
    started, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def delayed(route):
        # Fetch the actual response first, then deliberately deliver it out of order.
        try:
            original = await route.fetch()
            started.set()
            await asyncio.wait_for(release.wait(), timeout=15)
            await route.fulfill(response=original)
        except Error:
            # AbortController may cancel the superseded network request in Chromium.
            if not release.is_set():
                raise
        finally:
            finished.set()

    await page.goto(address(base, known, False))
    await page.route(target, delayed)
    await box(page, known).click()
    try:
        await asyncio.wait_for(started.wait(), timeout=10)
        await expect(
            page.locator('.inspector [data-decision="pending"]')
        ).to_be_visible()
        await expect(page.locator('.inspector img[src*="/references/"]')).to_have_count(
            0
        )
        await box(page, unknown).click()
        await decision(page, unknown)
    finally:
        release.set()
    await asyncio.wait_for(finished.wait(), timeout=10)
    await page.evaluate(
        "() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))"
    )
    await decision(page, unknown)
    await page.unroute(target, delayed)

    async def unavailable(route):
        await route.fulfill(
            status=503, json={"error": "Browser test: temporarily unavailable"}
        )

    await page.route(target, unavailable)
    await box(page, known).click()
    panel = page.locator(".inspector")
    await expect(panel.locator('[data-decision="unavailable"]')).to_be_visible()
    await expect(panel.get_by_text("ناشناخته", exact=True)).to_have_count(0)
    await expect(panel.locator('img[src*="/references/"]')).to_have_count(0)
    await capture(page, out, "comparison-request-failed")
    await page.unroute(target, unavailable)
    retry = panel.get_by_role("button", name=re.compile("تلاش دوباره|تلاش مجدد"))
    await (retry.first if await retry.count() else button(page, COMPARE)).click()
    await decision(page, known)


async def geometry_case(page, base, known, unknown, out, report):
    for width, height in VIEWPORTS:
        await page.set_viewport_size(dict(width=width, height=height))
        await page.goto(address(base, known, False))
        await expect(box(page, known)).to_be_visible()
        await within_viewport(page)
        await expect(page.locator(".vt-film img").first).to_be_attached()
        assert await page.locator(".vt-film img").evaluate_all(
            "imgs => new Set(imgs.map(i=>i.src)).size > 1"
        ), "Expected distinct decoded filmstrip frames"
        await capture(page, out, f"workspace-{width}")
        await box(page, known).click()
        await decision(page, known)
        error = max(await alignment(page, known), await alignment(page, unknown))
        assert error < 1.5, f"{width}px viewport: face overlay differs by {error}px"
        report["max_overlay_error_px"][str(width)] = round(error, 3)
        report["layout_css_px"][str(width)] = {
            name: await page.locator(selector).bounding_box()
            for name, selector in (
                ("video", ".stage"),
                ("inspector", ".inspector"),
                ("timeline", ".vt-timeline"),
            )
        }
        await within_viewport(page)
        assert await page.locator(".inspector [data-decision]").evaluate(
            "e=>getComputedStyle(e).animationName==='none' && getComputedStyle(e).transitionDuration==='0s'"
        ), "Selection still animates with reduced motion enabled"
        if width == 390:
            sheet = page.get_by_role("dialog")
            await expect(sheet).to_be_visible()
            assert await sheet.evaluate(
                's=>s.matches(":modal") || s.getAttribute("aria-modal")==="true"'
            )
            bounds = await sheet.bounding_box()
            assert bounds and abs(bounds["y"] + bounds["height"] - height) < 3, bounds
            await capture(page, out, "mobile-dialog-390")
            await page.keyboard.press("Tab")
            assert await sheet.evaluate("s=>s.contains(document.activeElement)")
            await page.keyboard.press("Shift+Tab")
            assert await sheet.evaluate("s=>s.contains(document.activeElement)")
            await sheet.locator('.face-list button[aria-pressed="false"]').click()
            await decision(page, unknown)
            await sheet.locator('.face-list button[aria-pressed="false"]').click()
            await decision(page, known)
            await page.keyboard.press("Escape")
            await expect(sheet).to_have_count(0)
            await expect(box(page, known)).to_be_focused()
            await box(page, unknown).click()
            await decision(page, unknown)
            await button(page, "بستن شناسایی چهره").click()
            await expect(sheet).to_have_count(0)
            await box(page, known).click()
            await decision(page, known)
            await button(page, APPEARANCES).click()
            await expect(sheet).to_have_count(0)
            await expect(
                page.locator(".appearances button[data-detection-id]").first
            ).to_be_visible()
            await within_viewport(page)
        else:
            await capture(page, out, f"selected-{width}")
    await page.set_viewport_size(dict(width=1440, height=900))
    await page.goto(address(base, known))
    await decision(page, known)
    await button(page, "تمام‌صفحه").click()
    await page.wait_for_function("!!document.fullscreenElement")
    await page.wait_for_function(
        "() => new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))"
    )
    fullscreen = await alignment(page, known)
    assert fullscreen < 1.5, fullscreen
    report["max_overlay_error_px"]["fullscreen"] = round(fullscreen, 3)
    await page.evaluate("document.exitFullscreen()")
    speed = page.get_by_role("combobox", name="سرعت پخش")
    await speed.select_option("1.5")
    await expect(page.locator(".stage video")).to_have_js_property("playbackRate", 1.5)
    await speed.select_option("1")
    await button(page, "پخش").click()
    await page.wait_for_function(
        "s=>{const v=document.querySelector('video');return !v.paused && v.currentTime>s+0.10}",
        arg=known["media_pts_s"],
    )
    await button(page, "توقف").click()
    t = await page.locator("video").evaluate("v=>v.currentTime")
    await button(page, "فریم بعدی").click()
    await page.wait_for_function(
        "t=>{const v=document.querySelector('video');return v.paused && v.currentTime>t}",
        arg=t,
    )
    await button(page, "فریم قبلی").click()
    await page.wait_for_function(
        "t=>document.querySelector('video').currentTime<=t+0.002", arg=t
    )
    seek = page.locator(".vt-seek")
    await seek.focus()
    await page.keyboard.press("Home")
    await page.wait_for_function(
        "document.querySelector('.stage video').currentTime<0.002"
    )
    await page.keyboard.press("ArrowRight")
    await page.wait_for_function(
        "document.querySelector('.stage video').currentTime>0.002"
    )
    await expect(page.locator(".vt-events button").first).to_be_visible()
    marker = page.locator(".vt-events button").last
    await marker.focus()
    await page.keyboard.press("Enter")
    await page.wait_for_function("document.querySelector('.stage video').currentTime>1")


async def photo_case(page, base, root, out, known):
    await page.goto(base + "/?view=search")
    probe = root / "research/upload/faks0.jpg"
    saved = json.loads((root / "research/upload.json").read_text())
    digest = sha256(probe.read_bytes()).hexdigest()
    assert any(
        r["gallery_label"] == "enrolled" and r["sha256"] == digest for r in saved
    )
    upload_started, finish_upload = asyncio.Event(), asyncio.Event()

    async def hold_upload(route):
        upload_started.set()
        await finish_upload.wait()
        await route.continue_()

    endpoint = "**/api/identity/probes"
    await page.route(endpoint, hold_upload)
    try:
        await page.locator("input[type=file]").set_input_files(probe)
        await asyncio.wait_for(upload_started.wait(), timeout=10)
        await page.locator("select").first.select_option("studio")
    finally:
        finish_upload.set()
    async with page.expect_response(
        lambda r: urlparse(r.url).path == "/api/identity/search"
    ) as event:
        await button(page, "جست‌وجوی حضورها").click()
    await page.unroute(endpoint, hold_upload)
    groups = (await (await event.value).json())["groups"]
    assert groups, "Expected an appearance from the independent enrolled probe"
    assert all(group["source_id"] == "studio" for group in groups)
    # Retrieval includes unknown gallery decisions. Open an actual ranked, known result.
    with database(root) as db:
        for index, group in enumerate(groups):
            hit = max(group["observations"], key=lambda r: r["score"])
            candidate = db.execute(
                OBSERVATION + " WHERE o.detection_id=?", (hit["detection_id"],)
            ).fetchone()
            if (
                candidate
                and candidate["state"] == "matched"
                and candidate["reference_sha256"] == known["reference_sha256"]
            ):
                row = observation(db, hit["detection_id"])
                break
        else:
            raise AssertionError(
                "No ranked known appearance for the reference-record journey"
            )
    await expect(page.locator(".hit")).to_have_count(len(groups))
    await capture(page, out, "independent-photo-ranked-results")
    await (
        page.locator(".hit")
        .nth(index)
        .get_by_role("button", name=re.compile("باز کردن لحظه"))
        .click()
    )
    await decision(page, row)
    await (
        page.locator(".inspector")
        .get_by_role("button", name=re.compile("پروندهٔ مرجع|مشاهده پرونده"))
        .click()
    )
    q = parse_qs(urlparse(page.url).query)
    assert q["person"] == [row["person_id"]] and q["version"] == [str(row["version"])]
    panel = page.locator(".gallery-person")
    portrait = panel.locator(
        f'img[src="/api/identity/references/{row["reference_id"]}/portrait"]'
    )
    await expect(portrait).to_be_visible()
    await expect(portrait).to_have_js_property("complete", True)
    assert await portrait.evaluate("img=>img.naturalWidth>0")
    display = re.sub(r"^Participant ", "شرکت‌کنندهٔ ", row["display_name"], flags=re.I)
    await expect(panel.locator("h3")).to_have_text(display)
    await capture(page, out, "matched-gallery-record")
    return {
        "probe_sha256": digest,
        "rank": index + 1,
        "groups": len(groups),
        "detection_id": row["detection_id"],
        "occurrence_id": row["occurrence_id"],
        "reference_id": row["reference_id"],
        "gallery_version": row["version"],
    }


async def engineering(page, ops_url, grafana_url, out):
    for name, url, heading in (
        ("operations", ops_url, "Pipeline operations"),
        ("grafana", grafana_url, "Frames per second"),
    ):
        response = await page.goto(url)
        assert response and response.ok, url
        await expect(page.get_by_text(heading, exact=True).first).to_be_visible(
            timeout=25000
        )
        if name == "grafana":
            await expect(
                page.get_by_text("Face observations per second", exact=True).first
            ).to_be_visible()
        await expect(page.locator("html")).to_have_attribute("lang", re.compile("^en"))
        assert await page.evaluate("getComputedStyle(document.body).direction") == "ltr"
        await capture(page, out, name + "-english")


async def run(args):
    root = args.data.resolve()
    known, unknown, empty, poor = fixtures(root)
    out = root / "screens/video-workspace"
    out.mkdir(parents=True, exist_ok=True, mode=0o700)
    report = {
        "operator_url": args.url,
        "max_overlay_error_px": {},
        "layout_css_px": {},
        "injected_conditions": ["late comparison response", "temporary HTTP 503"],
    }
    errors = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(executable_path=args.chrome)
        report["chromium"] = browser.version
        common = dict(viewport=dict(width=1440, height=900), reduced_motion="reduce")
        movie = (
            dict(record_video_dir=out, record_video_size=dict(width=1280, height=800))
            if args.record
            else {}
        )
        context = await browser.new_context(**common, **movie)
        page = await context.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        try:
            await desktop_case(
                page, args.url, root, out, known, unknown, empty, poor, report
            )
            report["operator_bundle"] = await page.locator(
                'script[type="module"][src]'
            ).first.get_attribute("src")
        except BaseException:
            await context.close()
            if page.video:
                await page.video.delete()
            raise
        await context.close()
        if args.record:
            await page.video.save_as(
                out / "selection-comparison-appearance-return.webm"
            )
            await page.video.delete()
        context = await browser.new_context(**common)
        page = await context.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        await faults(page, args.url, known, unknown, out)
        await geometry_case(page, args.url, known, unknown, out, report)
        report["uploaded_probe"] = await photo_case(page, args.url, root, out, known)
        assert not errors, errors
        await engineering(page, args.operations, args.grafana, out)
        await browser.close()
    report["status"] = "PASS"
    report["verified_utc"] = datetime.now(timezone.utc).isoformat()
    (out / "evidence.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data", type=Path, default=Path("private/operator"))
    p.add_argument("--url", default="http://127.0.0.1:8092")
    p.add_argument("--operations", default="http://127.0.0.1:8091/operations")
    p.add_argument(
        "--grafana",
        default="http://127.0.0.1:13000/d/visions-operator/operator-feed-and-search",
    )
    p.add_argument("--chrome", default=os.environ.get("CHROME_BIN"))
    p.add_argument("--record", action="store_true")
    args = p.parse_args()
    asyncio.run(run(args))
