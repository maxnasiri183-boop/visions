"""Isolated gallery performance fixture and browser/API measurements."""

import argparse
from contextlib import closing
from hashlib import sha256
import importlib.util
import json
import math
import os
from pathlib import Path
import sqlite3
import sys
import time
from urllib.parse import urlencode, urlsplit
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "private" / "gallery-scale"
SEED = 34117
PAGE = 24
PERSON = "scale-{:06d}"


def save(path, value):
    temp = path.with_suffix(".new")
    temp.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
    temp.replace(path)


def fixture_root(size):
    if size not in (100000, 300000):
        raise ValueError("Expected 100000 or 300000 synthetic people")
    return BASE / str(size)


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def setup_env(folder, code):
    os.environ.update(OPERATOR_ROOT=str(folder), OPERATOR_DATA=str(folder))
    sys.path.insert(0, str(code))


def fixture(size, code):
    import numpy as np
    from PIL import Image, ImageDraw

    folder = fixture_root(size)
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    record = folder / "fixture.json"
    if record.is_file():
        print(record.read_text())
        return
    if (folder / "identity.sqlite").exists():
        raise ValueError(
            f"Partial fixture exists: {folder}. Select a fresh isolated data directory."
        )
    setup_env(folder, code)
    from identity import core as c

    if (code / "apps/feed/src").is_dir():
        sys.path.insert(0, str(code / "apps/feed/src"))
        from visions_feed import db as feed
    else:
        # The scale harness intentionally benchmarks historical checkouts.
        sys.path.insert(0, str(code / "feed"))
        import db as feed

    feed.initialize()
    c.init()
    # Shared nonface thumbnails represent a warm CDN/storage working set.
    # Each logical portrait request still passes through the real image endpoint.
    tiles = []
    for i in range(32):
        im = Image.new("RGB", (112, 112), (44 + i * 3, 65 + i * 2, 88 + i * 2))
        surface = ImageDraw.Draw(im)
        surface.rectangle((12 + i % 7, 25, 68, 81), fill=(161, 190, 198))
        surface.ellipse((47, 39 + i % 11, 98, 90), fill=(194 - i, 151, 116 + i))
        directory = folder / "tiles"
        directory.mkdir(exist_ok=True)
        path = directory / f"{i:02}.jpg"
        im.save(path, quality=82)
        tiles.append(
            (str(path.relative_to(folder)), sha256(path.read_bytes()).hexdigest())
        )
    now = 1795482000.0
    rng = np.random.default_rng(SEED)
    started = time.perf_counter()
    with closing(sqlite3.connect(folder / "identity.sqlite")) as db:
        db.execute("PRAGMA foreign_keys=ON")
        for version in (1, 2):
            db.execute(
                "INSERT INTO versions VALUES(?,?,?,?,?,?,?,?)",
                (
                    version,
                    now + version,
                    0.5,
                    0.5,
                    c.MODEL,
                    c.PRE,
                    c.POLICY,
                    '{"method":"synthetic fixture"}',
                ),
            )
        db.commit()
        for start in range(1, size + 1, 2000):
            records, lineage, members = [], [], []
            for n in range(start, min(size + 1, start + 2000)):
                person = PERSON.format(n)
                states = ["rejected" if n % 17 == 0 else "accepted"]
                if n % 5 == 0:
                    states.append("rejected")
                if n % 10 == 0 and n % 17:
                    states.append("accepted")
                for j, state in enumerate(states):
                    rid = f"{person}-r{j}"
                    path, digest = tiles[(n + j) % len(tiles)]
                    vec = None
                    if state == "accepted":
                        arr = rng.standard_normal(128, dtype=np.float32)
                        vec = (arr / np.linalg.norm(arr)).astype("<f4").tobytes()
                        if n <= size // 2:
                            members.append((1, rid))
                        members.append((2, rid))
                    records.append(
                        (
                            rid,
                            person,
                            f"Synthetic Person {n:06d}",
                            f"synthetic-session-{j}",
                            state,
                            None if vec else "Face or eye separation too small",
                            path,
                            digest,
                            vec,
                            now + n / size + j / 10,
                        )
                    )
                    lineage.append(
                        (
                            rid,
                            c.MODEL,
                            c.PRE,
                            c.POLICY,
                            "[0,0,112,112]" if vec else None,
                            path if vec else None,
                        )
                    )
            with db:
                db.executemany("INSERT INTO refs VALUES(?,?,?,?,?,?,?,?,?,?)", records)
                db.executemany(
                    "INSERT INTO reference_features VALUES(?,?,?,?,?,?)", lineage
                )
                db.executemany("INSERT INTO membership VALUES(?,?)", members)
        refs = db.execute(
            "SELECT count(*),sum(status='accepted'),sum(status='rejected'),count(DISTINCT person_id) FROM refs"
        ).fetchone()
        assert refs[-1] == size
        membership = dict(
            db.execute("SELECT version,count(*) FROM membership GROUP BY version")
        )
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    result = dict(
        people=size,
        references=refs[0],
        accepted=refs[1],
        rejected=refs[2],
        memberships=membership,
        seed=SEED,
        dtype="little-endian float32",
        dimensions=128,
        thumbnail_tiles=32,
        identity_db_bytes=(folder / "identity.sqlite").stat().st_size,
        generation_seconds=time.perf_counter() - started,
    )
    save(record, result)
    print(json.dumps(result), flush=True)


def exact(size, code, count):
    import numpy as np

    folder = fixture_root(size)
    setup_env(folder, code)
    from identity import core, worker

    with core.connect() as db:
        tic = time.perf_counter()
        rows = db.execute(
            "SELECT * FROM refs JOIN membership USING(reference_id) WHERE version=2"
        ).fetchall()
        load_ms = (time.perf_counter() - tic) * 1000
    selected = np.random.default_rng(SEED).choice(len(rows), size=count, replace=False)
    tic = time.perf_counter()
    matrix = np.asarray([np.frombuffer(row["embedding"], dtype="<f4") for row in rows])
    matrix_ms = (time.perf_counter() - tic) * 1000
    loop, contiguous = [], []
    for index in selected:
        query = rows[index]["embedding"]
        tic = time.perf_counter()
        state, ref, score, _ = worker.classify(query, rows, 0.5)
        loop.append((time.perf_counter() - tic) * 1000)
        tic = time.perf_counter()
        scores = matrix @ np.frombuffer(query, dtype="<f4")
        best = int(np.argmax(scores))
        contiguous.append((time.perf_counter() - tic) * 1000)
        if (
            state != "matched"
            or ref != rows[index]["reference_id"]
            or best != index
            or abs(scores[best] - score) > 1e-5
        ):
            raise AssertionError(
                "Exact top-1 implementations disagree on fixture self-query"
            )
    result = dict(
        references=len(rows),
        dimensions=128,
        active_version=2,
        BLAS_threads=os.environ.get("OPENBLAS_NUM_THREADS"),
        load_reference_rows_ms=load_ms,
        matrix_build_ms=matrix_ms,
        contiguous_bytes=matrix.nbytes,
        production_classify_ms=summary(loop),
        isolated_numpy_exact_ms=summary(contiguous),
        validated_self_queries=count,
    )
    save(folder / "exact-vector.json", result)
    print(json.dumps(result), flush=True)


def worker_vectors(size, code, count):
    import resource
    import numpy as np

    folder = fixture_root(size)
    setup_env(folder, code)
    from identity import core, worker
    from identity.matching import ExactGallery

    def rss_bytes():
        return (
            int(
                next(
                    x.split()[1]
                    for x in Path("/proc/self/status").read_text().splitlines()
                    if x.startswith("VmRSS:")
                )
            )
            * 1024
        )

    gallery = ExactGallery()
    with core.connect() as db:
        before = rss_bytes()
        start = time.perf_counter()
        gallery.load(db, 2)
        cold_ms = (time.perf_counter() - start) * 1000
        retained_rss_delta = rss_bytes() - before
        load_high_water = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
        start = time.perf_counter()
        gallery.load(db, 2)
        cached_us = (time.perf_counter() - start) * 1e6
        rng = np.random.default_rng(SEED)
        indices = rng.choice(len(gallery.ids), size=count, replace=False)
        vectors = [
            db.execute(
                "SELECT embedding FROM refs WHERE reference_id=?", (gallery.ids[i],)
            ).fetchone()[0]
            for i in indices
        ]
    timings = []
    for q, index in zip(vectors, indices):
        start = time.perf_counter()
        result = gallery.classify(q, 0.5)
        timings.append((time.perf_counter() - start) * 1000)
        assert (
            result[0] == "matched"
            and result[1] == gallery.ids[index]
            and result[2] > 0.99999
        )
    # Compare distinct near-reference and random nonmember probes with the original scalar loop.
    with core.connect() as db:
        rows = db.execute(
            "SELECT reference_id,embedding FROM refs JOIN membership USING(reference_id) WHERE version=2 ORDER BY reference_id"
        ).fetchall()
    checks = []
    for step in range(4):
        q = rng.standard_normal(128, dtype=np.float32)
        if step % 2:
            q = q * 0.01 + np.frombuffer(vectors[step], "<f4")
        q = (q / np.linalg.norm(q)).astype("<f4").tobytes()
        scalar = worker.classify(q, rows, 0.5)
        optimized = gallery.classify(q, 0.5)
        assert scalar[:2] == optimized[:2] and abs(scalar[2] - optimized[2]) < 1e-6
        checks.append(
            dict(state=optimized[0], abs_score_error=abs(scalar[2] - optimized[2]))
        )
    result = dict(
        references=len(gallery.ids),
        version=2,
        dimensions=128,
        blas_threads=os.environ.get("OPENBLAS_NUM_THREADS"),
        matrix_bytes=gallery.matrix.nbytes,
        retained_rss_delta_bytes=retained_rss_delta,
        process_high_water_bytes_after_load=load_high_water,
        version_load_ms=cold_ms,
        reuse_same_version_microseconds=cached_us,
        lookup_ms=summary(timings),
        exact_self_checks=len(timings),
        parity_probes=checks,
    )
    save(folder / "worker-vectors.json", result)
    print(json.dumps(result), flush=True)


def summary(samples):
    ordered = sorted(samples)
    if not ordered:
        return dict(n=0)
    return dict(
        n=len(samples),
        p50=ordered[math.ceil(0.5 * len(samples)) - 1],
        p95=ordered[math.ceil(0.95 * len(samples)) - 1],
        min=ordered[0],
        max=ordered[-1],
    )


def serve(size, code, port):
    import resource
    from waitress import serve as run

    # Bound this test server's address space independently of the live operator service.
    resource.setrlimit(resource.RLIMIT_AS, (4 * 1024**3, 4 * 1024**3))
    folder = fixture_root(size)
    setup_env(folder, code)
    os.environ["GALLERY_FIXTURE"] = "1"
    app = load_module(
        "gallery_scale_operator_server", code / "operator" / "server.py"
    ).create_app()
    run(app, host="127.0.0.1", port=port, threads=4)


def get(path, base):
    tic = time.perf_counter()
    with urlopen(base + path, timeout=60) as res:
        data = res.read()
    return dict(ms=(time.perf_counter() - tic) * 1000, bytes=len(data))


def api_profile(base, size, mode, reps):
    folder = fixture_root(size)
    paths = (
        {"entire_gallery": "/api/identity/gallery"}
        if mode == "baseline"
        else {
            "first": "/api/identity/people?limit=24",
            "draft_status": "/api/identity/people?limit=24&status=rejected",
            "exact_id": "/api/identity/people?"
            + urlencode({"limit": 24, "q": PERSON.format(size - 2)}),
            "visible_name": "/api/identity/people?"
            + urlencode({"limit": 24, "q": "Synthetic Person 09999"}),
            "snapshot": "/api/identity/people?limit=24&version=1",
            "detail": "/api/identity/people/" + PERSON.format(size - 2) + "?version=2",
        }
    )
    if mode != "baseline":
        with urlopen(base + paths["first"]) as res:
            first = json.load(res)
        if len(first["people"]) != PAGE or not first.get("next_cursor"):
            raise AssertionError("Expected a bounded first page with a continuation")
        paths["cursor"] = "/api/identity/people?" + urlencode(
            {"limit": PAGE, "after": first["next_cursor"]}
        )
    stats = {}
    for name, path in paths.items():
        runs = [get(path, base) for _ in range(reps)]
        stats[name] = dict(ms=summary([r["ms"] for r in runs]), bytes=runs[0]["bytes"])
    save(folder / (mode + "-api.json"), stats)
    print(json.dumps(stats), flush=True)


def browser_profile(args):
    from playwright.sync_api import TimeoutError as PlaywrightTimeout
    from playwright.sync_api import sync_playwright

    base = "http://127.0.0.1:" + str(args.port)
    folder = fixture_root(args.size)
    baseline = args.mode == "baseline"
    viewport = (
        {"width": 390, "height": 844} if args.mobile else {"width": 1365, "height": 900}
    )
    report = {
        "mode": args.mode,
        "fixture_people": args.size,
        "viewport": [viewport["width"], viewport["height"]],
        "browser_process": "Headless Chrome",
        "first_image_deadline_ms": 30000,
        "cold": [],
        "warm": [],
    }
    output = folder / (args.mode + ("-mobile" if args.mobile else "") + "-browser.json")
    target_id = PERSON.format(args.size - 2)
    target_name = "Synthetic Person " + f"{args.size - 2:06d}"

    def measured(call):
        start = time.perf_counter()
        call()
        return (time.perf_counter() - start) * 1000

    def ready(page, selector=".portrait-grid button"):
        page.locator(selector).first.wait_for(state="visible", timeout=30000)
        page.wait_for_function(
            """s => {const el=document.querySelector(s), img=el?.querySelector('img');
            return el && (!img || (img.complete && img.naturalWidth>0));}""",
            arg=selector,
            timeout=30000,
        )
        page.evaluate(
            "() => new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))"
        )

    def measure(page, context, cache, group):
        cdp = context.new_cdp_session(page)
        cdp.send("Network.enable")
        cdp.send("Network.setCacheDisabled", {"cacheDisabled": cache == "cold"})
        cdp.send("Performance.enable")
        requests = {}

        def request(e):
            u = urlsplit(e["request"]["url"])
            requests[e["requestId"]] = {"path": u.path, "wire": 0, "body": 0}

        def response(e):
            item = requests.get(e["requestId"])
            if item:
                head = {k.lower(): v for k, v in e["response"]["headers"].items()}
                item.update(
                    status=e["response"]["status"],
                    body=int(head.get("content-length", 0)),
                )

        def finish(e):
            if e["requestId"] in requests:
                requests[e["requestId"]]["wire"] = int(e["encodedDataLength"])

        cdp.on("Network.requestWillBeSent", request)
        cdp.on("Network.responseReceived", response)
        cdp.on("Network.loadingFinished", finish)
        timing = {}
        sample = {"timings_ms": timing}
        report[group].append(sample)
        started = time.perf_counter()
        try:
            page.goto(base + "/?view=gallery", wait_until="commit", timeout=10000)
            ready(page)
            timing["first_image"] = (time.perf_counter() - started) * 1000
            sample["cards_first"] = page.locator(".portrait-grid button").count()
            sample["dom_elements_first"] = page.evaluate(
                "document.getElementsByTagName('*').length"
            )
            heap = cdp.send("Runtime.getHeapUsage")
            sample["js_heap_used_bytes_first"] = heap["usedSize"]
        except PlaywrightTimeout as error:
            sample.update(
                first_image_timeout=True,
                elapsed_ms=(time.perf_counter() - started) * 1000,
                timeout_reason=str(error).splitlines()[0],
            )

        def traffic():
            assets = list(requests.values())
            api = [x for x in assets if x["path"].startswith("/api/")]
            return dict(
                requests=len(assets),
                wire_bytes=sum(x["wire"] for x in assets),
                api_requests=len(api),
                api_wire_bytes=sum(x["wire"] for x in api),
                portraits=sum(
                    x["path"].endswith(("/face", "/face-thumbnail", "/portrait", "/thumbnail")) for x in assets
                ),
                gallery_body_bytes=sum(
                    x["body"]
                    for x in assets
                    if x["path"]
                    in (
                        "/api/identity/gallery",
                        "/api/identity/people",
                        "/api/identity/versions",
                    )
                ),
            )

        sample["first_network"] = traffic()
        # Browser-process CDP remains responsive even if React saturates the renderer.
        system = context.browser.new_browser_cdp_session()
        process_info = system.send("SystemInfo.getProcessInfo")["processInfo"]
        model = {
            "processes": 0,
            "rss_sum_bytes": 0,
            "pss_sum_bytes": 0,
            "renderer_high_water_max_bytes": 0,
        }
        for proc in process_info:
            try:
                directory = Path("/proc") / str(proc["id"])
                mem = {
                    x.split(":")[0]: int(x.split()[1]) * 1024
                    for x in (directory / "status").read_text().splitlines()
                    if x.startswith(("VmRSS:", "VmHWM:"))
                }
                pss = next(
                    int(x.split()[1]) * 1024
                    for x in (directory / "smaps_rollup").read_text().splitlines()
                    if x.startswith("Pss:")
                )
            except (
                FileNotFoundError,
                ProcessLookupError,
                PermissionError,
                StopIteration,
            ):
                continue
            model["processes"] += 1
            model["rss_sum_bytes"] += mem.get("VmRSS", 0)
            model["pss_sum_bytes"] += pss
            if proc["type"] == "renderer":
                model["renderer_high_water_max_bytes"] = max(
                    model["renderer_high_water_max_bytes"], mem.get("VmHWM", 0)
                )
        system.detach()
        sample["browser_process_memory_first"] = model
        save(output, report)
        if baseline:
            return
        if sample.get("first_image_timeout"):
            raise AssertionError("Paged fixture failed to render")
        if sample["cards_first"] != PAGE:
            raise AssertionError(
                f"Unexpected person card count: {sample['cards_first']}"
            )
        if page.evaluate("document.documentElement.dir") != "rtl":
            raise AssertionError("Gallery document direction must be RTL")
        width = page.viewport_size["width"]

        def fits():
            return page.evaluate("document.documentElement.scrollWidth") <= width

        assert fits(), "Horizontal overflow on gallery grid"
        cards = page.locator(".portrait-grid button")
        first = cards.locator("strong").first.text_content()
        next_button = page.get_by_role("button", name="صفحهٔ بعد", exact=True)
        prev_button = page.get_by_role("button", name="صفحهٔ قبل", exact=True)

        def turn(button, previous, expected=None):
            button.click()
            page.wait_for_function(
                """([previous,expected])=>{
                const name=document.querySelector('.portrait-grid button strong')?.textContent;
                return name && (expected?name===expected:name!==previous)}""",
                arg=[previous, expected],
            )
            ready(page)

        timing["next_page"] = measured(lambda: turn(next_button, first))
        second = cards.locator("strong").first.text_content()
        timing["second_next_page"] = measured(lambda: turn(next_button, second))
        third = cards.locator("strong").first.text_content()
        assert len({first, second, third}) == 3
        timing["third_to_second"] = measured(lambda: turn(prev_button, third, second))
        timing["previous_page"] = measured(lambda: turn(prev_button, second, first))
        if cards.count() != PAGE:
            raise AssertionError("Cursor round trip changed first-page count")
        # Sample actual Chromium rAF timestamps while stepping through the full first grid.
        frame_intervals = page.evaluate("""async () => {
          window.scrollTo(0,0);const last=[...document.querySelectorAll('.portrait-grid button')].at(-1);
          const top=Math.min(document.documentElement.scrollHeight-innerHeight,
                   Math.max(0,last.getBoundingClientRect().top+scrollY-innerHeight/2));
          const t=[],frame=()=>new Promise(r=>requestAnimationFrame(r));
          let before=await frame();
          for(let i=1;i<=12;i++){window.scrollTo(0,top*i/12);const now=await frame();t.push(now-before);before=now;}
          return t;}""")
        sample["scroll_frame_ms"] = summary(frame_intervals)
        selected = cards.last
        selected_name = selected.locator("strong").text_content()
        selected.scroll_into_view_if_needed()
        saved_y = page.evaluate("scrollY")

        def inspect_scrolled():
            selected.click()
            page.locator(".gallery-person").get_by_text(
                selected_name, exact=True
            ).wait_for()
            ready(page, ".gallery-person")

        timing["open_scrolled_person"] = measured(inspect_scrolled)
        assert fits(), "Horizontal overflow in person detail"
        sample["detail_dom_elements"] = page.evaluate(
            "document.getElementsByTagName('*').length"
        )
        sample["detail_js_heap_used_bytes"] = cdp.send("Runtime.getHeapUsage")[
            "usedSize"
        ]

        def restore():
            page.go_back(wait_until="commit")
            page.locator(".gallery-person").wait_for(state="hidden")
            ready(page)
            page.wait_for_function("y=>Math.abs(scrollY-y)<5", arg=saved_y)

        timing["restore_scrolled_list"] = measured(restore)
        sample["restored_scroll_css_pixels"] = round(saved_y)
        controls = page.locator(".gallery-tools select")

        def filter_status():
            controls.nth(1).select_option("rejected")
            page.wait_for_function("""()=>{let els=[...document.querySelectorAll('.portrait-grid button small')];
                return els.length===24 && els.every(e=>e.textContent.includes('ردشده'))}""")
            ready(page)

        timing["rejected_filter"] = measured(filter_status)
        controls.nth(1).select_option("")
        page.wait_for_function(
            "v=>document.querySelector('.portrait-grid button strong')?.textContent===v",
            arg=first,
        )

        def search():
            page.get_by_role("searchbox").fill(target_id)
            page.locator(".portrait-grid button").filter(
                has_text=target_name
            ).first.wait_for()
            ready(page)

        timing["search_exact_id"] = measured(search)
        if page.locator(".portrait-grid button").count() != 1:
            raise AssertionError("Expected one exact synthetic person")

        def person():
            page.locator(".portrait-grid button").first.click()
            page.locator(".gallery-person").get_by_text(
                target_name, exact=True
            ).wait_for()
            ready(page, ".gallery-person")

        timing["person_detail"] = measured(person)

        def back():
            page.go_back(wait_until="commit")
            page.locator(".gallery-person").wait_for(state="hidden")
            page.locator(".portrait-grid button").filter(
                has_text=target_name
            ).first.wait_for()
            ready(page)

        timing["history_back"] = measured(back)
        if page.get_by_role("searchbox").input_value() != target_id:
            raise AssertionError("Search context lost on back")

        def deep():
            page.goto(
                base
                + "/?"
                + urlencode({"view": "gallery", "person": target_id, "version": "2"}),
                wait_until="commit",
            )
            page.locator(".gallery-person").get_by_text(
                target_name, exact=True
            ).wait_for()
            ready(page, ".gallery-person")

        timing["deep_link"] = measured(deep)
        sample["journey_network"] = traffic()
        sample["errors"] = [x for x in requests.values() if x.get("status", 0) >= 400]
        app_errors = [
            failure for failure in sample["errors"] if failure["path"] != "/favicon.ico"
        ]
        assert not app_errors, app_errors
        save(output, report)
        cdp.detach()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            executable_path=args.chrome, args=["--js-flags=--max-old-space-size=1536"]
        )
        report["browser_version"] = browser.version
        cold_reps = 1 if baseline else min(args.repetitions, 8)
        for _ in range(cold_reps):
            context = browser.new_context(viewport=viewport)
            page = context.new_page()
            page.set_default_timeout(10000)
            measure(page, context, "cold", "cold")
            if baseline and report["cold"][-1].get("first_image_timeout"):
                # CDP close avoids waiting for the blocked renderer's main thread.
                browser.close()
                print(json.dumps(report, ensure_ascii=False), flush=True)
                return
            context.close()
        if not baseline:
            context = browser.new_context(viewport=viewport)
            page = context.new_page()
            page.goto(base + "/?view=gallery", wait_until="commit")
            ready(page)
            for _ in range(args.repetitions):
                measure(page, context, "warm", "warm")
            context.close()
        browser.close()
    for group in ("cold", "warm"):
        timings = [s["timings_ms"] for s in report[group]]
        keys = sorted({k for sample in timings for k in sample})
        report[group + "_timings_ms"] = {
            k: summary([s[k] for s in timings if k in s]) for k in keys
        }
    save(output, report)
    print(
        json.dumps({k: v for k, v in report.items() if k.endswith("_timings_ms")}),
        flush=True,
    )


def cold_api(size, code, mode, count):
    import socket
    import subprocess

    route = (
        "/api/identity/gallery"
        if mode == "baseline"
        else "/api/identity/people?limit=24"
    )
    times = []
    for _ in range(count):
        with socket.socket() as temporary:
            temporary.bind(("127.0.0.1", 0))
            port = temporary.getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        server = subprocess.Popen(
            [
                sys.executable,
                __file__,
                "serve",
                "--size",
                str(size),
                "--port",
                str(port),
                "--code",
                str(code),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            cutoff = time.monotonic() + 30
            while True:
                if server.poll() is not None or time.monotonic() > cutoff:
                    raise RuntimeError("Isolated fresh server did not become ready")
                try:
                    get("/health", base)
                    break
                except OSError:
                    time.sleep(0.05)
            times.append(get(route, base))
        finally:
            server.terminate()
            server.wait(timeout=8)
    result = dict(
        first_response_ms=summary([x["ms"] for x in times]),
        bytes=times[0]["bytes"],
        process=f"{count} fresh Waitress processes, existing SQLite and OS page cache",
    )
    save(fixture_root(size) / (mode + "-process-cold.json"), result)
    print(json.dumps(result), flush=True)


def sql_plans(size, code):
    """Observe executed SQLite statements with real Flask routes and an actual cursor."""
    import base64
    from unittest.mock import patch

    folder = fixture_root(size)
    setup_env(folder, code)
    os.environ["GALLERY_FIXTURE"] = "1"
    app = load_module(
        "gallery_scale_plan_server", code / "operator" / "server.py"
    ).create_app()
    client = app.test_client()
    first = client.get("/api/identity/people?limit=24")
    assert first.status_code == 200, first.data[:200]
    cursor = first.json["next_cursor"]
    first_ids = set(p["person_id"] for p in first.json["people"])
    actual_connect = sqlite3.connect
    captured = []
    chunks = 0

    def traced(*args, **kwargs):
        conn = actual_connect(*args, **kwargs)

        def count_chunk():
            nonlocal chunks
            chunks += 1
            return 0

        conn.set_progress_handler(count_chunk, 100)
        conn.set_trace_callback(captured.append)
        return conn

    output = {}

    def examine(label, path):
        nonlocal chunks
        captured.clear()
        chunks = 0
        with patch("sqlite3.connect", traced):
            res = client.get(path)
        assert res.status_code == 200, (label, res.data[:200])
        plans = []
        with closing(
            actual_connect(f"file:{folder}/identity.sqlite?mode=ro", uri=True)
        ) as db:
            for query in captured:
                if query.strip().upper().startswith("SELECT"):
                    plan = [row[3] for row in db.execute("EXPLAIN QUERY PLAN " + query)]
                    plans.append(dict(sql=query, plan=plan))
        output[label] = dict(
            bytes=len(res.data),
            vm_steps_approx=chunks * 100,
            selects=len(plans),
            queries=plans,
        )
        return res.json

    def people(**kwargs):
        return "/api/identity/people?" + urlencode({"limit": PAGE, **kwargs})

    examine("first", people())
    following = examine("next", people(after=cursor))
    assert first_ids.isdisjoint(p["person_id"] for p in following["people"])
    tail_prefix = PERSON.format(size - 100)[:-2]
    matching = examine("deep_prefix", people(q=tail_prefix))
    match_next = examine(
        "deep_prefix_real_next", people(q=tail_prefix, after=matching["next_cursor"])
    )
    assert all(p["person_id"].startswith(tail_prefix) for p in match_next["people"])
    assert {x["person_id"] for x in matching["people"]}.isdisjoint(
        x["person_id"] for x in match_next["people"]
    )
    predecessor = PERSON.format(size - 40)
    # Stress an unfiltered, far-from-origin continuation independently of cursor traversal.
    vals = json.loads(base64.urlsafe_b64decode(cursor).decode())
    vals[-2:] = [predecessor, predecessor]
    tail = base64.urlsafe_b64encode(json.dumps(vals).encode()).decode()
    deep = examine("unfiltered_tail_seeded_predecessor", people(after=tail))
    assert deep["people"][0]["person_id"] == PERSON.format(size - 39)
    name_match = examine(
        "name_prefix", people(q=f"Synthetic Person {size - 100:06d}"[:-2])
    )
    assert len(name_match["people"]) == PAGE
    empty = examine("no_match", people(q="absent-scale-fixture"))
    assert not empty["people"]
    rejected = examine("rejected", people(status="rejected"))
    assert all(x["status"] == "rejected" for x in rejected["people"])
    draft = examine("draft_exact", people(q=PERSON.format(size - 2)))
    assert [x["person_id"] for x in draft["people"]] == [PERSON.format(size - 2)]
    old = examine(
        "snapshot_before_enrollment", people(q=PERSON.format(size - 2), version=1)
    )
    assert not old["people"]
    detail = examine(
        "person_detail",
        "/api/identity/people/" + PERSON.format(size - 2) + "?version=2",
    )
    assert detail["references"] and all(
        r["person_id"] == PERSON.format(size - 2) for r in detail["references"]
    )
    examine("versions", "/api/identity/versions")
    assert client.get(people(limit=10000000)).status_code == 400
    assert client.get(people(q="new-query", after=cursor)).status_code == 400
    save(folder / "paged-sql-plans.json", output)
    print(json.dumps({k: v["vm_steps_approx"] for k, v in output.items()}), flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "action",
        choices=(
            "fixture",
            "vectors",
            "worker-vectors",
            "serve",
            "api",
            "cold-api",
            "plans",
            "browser",
        ),
    )
    p.add_argument("--size", type=int, choices=(100000, 300000), required=True)
    p.add_argument("--code", type=Path, default=ROOT)
    p.add_argument("--mode", choices=("baseline", "paged"), default="paged")
    p.add_argument("--port", type=int, default=19811)
    p.add_argument("--repetitions", type=int, default=10)
    p.add_argument(
        "--mobile", action="store_true", help="Measure the 390-pixel RTL viewport"
    )
    p.add_argument("--chrome", default=os.getenv("CHROME_BIN"))
    a = p.parse_args()
    if a.repetitions < 1:
        p.error("--repetitions must be positive")
    code = a.code.resolve()
    if a.action == "fixture":
        fixture(a.size, code)
    elif a.action == "vectors":
        exact(a.size, code, a.repetitions)
    elif a.action == "worker-vectors":
        worker_vectors(a.size, code, a.repetitions)
    elif a.action == "serve":
        serve(a.size, code, a.port)
    elif a.action == "api":
        api_profile("http://127.0.0.1:" + str(a.port), a.size, a.mode, a.repetitions)
    elif a.action == "cold-api":
        cold_api(a.size, code, a.mode, a.repetitions)
    elif a.action == "plans":
        sql_plans(a.size, code)
    else:
        browser_profile(a)


if __name__ == "__main__":
    main()
