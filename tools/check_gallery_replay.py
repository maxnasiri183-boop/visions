"""Verify a versioned portrait, retained moment, saved SQL evidence and on-demand cosine."""

import argparse
import json
import os
from urllib.parse import parse_qs, urlencode, urlparse

from playwright.sync_api import expect, sync_playwright


def verify(args):
    gallery = (
        args.url
        + "/?"
        + urlencode(dict(view="gallery", version=args.version, person=args.person))
    )
    with sync_playwright() as driver:
        browser = driver.chromium.launch(executable_path=args.chrome)
        page = browser.new_page(
            viewport={"width": 1440, "height": 900}, reduced_motion="reduce"
        )
        page.goto(gallery)
        panel = page.locator(".gallery-person")
        portrait = panel.locator("figure img").first
        expect(portrait).to_be_visible()
        assert portrait.evaluate("e=>e.complete && e.naturalWidth>0")
        panel.get_by_role("button", name="باز کردن ویدئو", exact=False).first.click()
        page.locator(".box[aria-pressed=true]").first.click()
        page.get_by_role("button", name="مقایسه با گالری", exact=True).click()
        page.locator("[data-decision=matched]").wait_for()
        query = parse_qs(urlparse(page.url).query)
        identifier = query["face"][0]
        saved = page.request.get(
            args.url + "/api/identity/observations/" + identifier
        ).json()
        assert saved["person_id"] == args.person and saved["version"] == args.version
        expect(page.locator(".pair img")).to_have_count(2)
        page.get_by_text("جزئیات فنی", exact=True).first.click()
        expect(page.locator(".technical pre")).to_contain_text(identifier)
        payload = json.loads(page.locator(".technical pre").inner_text())
        assert (
            payload["sql"]["detection_id"]
            == payload["event"]["detection_id"]
            == identifier
        )
        with page.expect_response(
            lambda r: r.url.endswith(identifier + "/compare")
        ) as response:
            page.get_by_role("button", name="محاسبهٔ مجدد شباهت دو تصویر").click()
        fresh = response.value.json()
        assert (
            fresh["method"] == "on_demand_pair_cosine"
            and fresh["reference_id"] == saved["reference_id"]
        )
        assert abs(fresh["score"] - saved["score"]) < 1e-5 and fresh["above_threshold"]
        assert (
            page.request.get(
                args.url + "/api/identity/observations/" + identifier
            ).json()
            == saved
        )
        proof = dict(
            portrait=gallery,
            reference_url=saved["portrait_url"],
            recording=page.url,
            detection_id=identifier,
            saved_score=saved["score"],
            recomputed_score=fresh["score"],
            threshold=saved["threshold"],
            gallery_version=saved["version"],
            model=saved["model"],
            sql_rows_inspected=1,
            revision=page.request.get(args.url + "/health").json().get("revision"),
        )
        browser.close()
    print(json.dumps(proof, indent=2))


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--url", default="http://127.0.0.1:8092")
    p.add_argument("--version", type=int, required=True)
    p.add_argument("--person", required=True)
    p.add_argument("--chrome", default=os.environ.get("CHROME_BIN"))
    verify(p.parse_args())
