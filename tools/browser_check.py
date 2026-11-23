"""Exercise the running Review workspace. Creates one face run and one blank run."""

import argparse

from playwright.sync_api import expect, sync_playwright


def start(page, frame):
    page.get_by_role("button", name="New run").click()
    page.get_by_label("Inference frame").fill(str(frame))
    page.get_by_label("Feed playback speed").select_option("2")
    page.get_by_role("button", name="Start run", exact=True).click()
    expect(page.locator("#status")).to_have_text("Live job")
    expect(page.locator("#hint")).to_have_text("Result pending.")
    expect(page.locator("#status")).to_have_text("Saved run", timeout=210_000)


def check(url, executable):
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=executable)
        page = browser.new_page(viewport={"width": 1360, "height": 900})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(url)
        start(page, 5)
        box = page.get_by_role("button", name="Inspect detected face")
        box.click()
        expect(page.locator("#crop")).to_have_js_property("naturalWidth", 92)
        identity = page.get_by_label("Detection ID").input_value()
        page.locator("#sql summary").click()
        expect(page.locator("#row")).to_contain_text(identity)
        for width in [1360, 390]:
            page.set_viewport_size({"width": width, "height": 900})
            face = box.bounding_box()
            image = page.locator(".surface").bounding_box()
            assert abs(face["x"] - image["x"] - image["width"] * 178 / 512) < 1
            assert page.evaluate("document.body.scrollWidth <= innerWidth")
        page.get_by_role("button", name="Next frame").click()
        expect(page.locator("#hint")).to_have_text("Frame not processed.")
        page.get_by_role("button", name="Play", exact=True).click()
        page.wait_for_function("document.querySelector('video').currentTime > 0.8")
        page.get_by_role("button", name="Pause", exact=True).click()
        page.locator("#slider").press("Home")
        for _ in range(5):
            page.locator("#slider").press("ArrowRight")
        box.click()
        page.get_by_role("button", name="Replay observation").click()
        expect(page.locator("#evidence")).to_contain_text(
            "1 logical row after replay", timeout=210_000
        )
        print(identity, page.locator("#evidence").inner_text(), flush=True)
        page.reload()
        box.click()
        expect(page.get_by_label("Detection ID")).to_have_value(identity)
        start(page, 15)
        expect(page.locator("#hint")).to_have_text("Processed with zero faces.")
        assert not errors, errors
        browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8091")
    parser.add_argument("--chromium", help="Path to an installed Chromium browser")
    args = parser.parse_args()
    check(args.url, args.chromium)
