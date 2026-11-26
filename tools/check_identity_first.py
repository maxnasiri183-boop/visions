"""Exercise the identity-first operator API against an isolated labeled two-face fixture."""

import json
import os
import tempfile
from pathlib import Path

import numpy as np

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    os.environ.update(OPERATOR_DATA=tmp, OPERATOR_ROOT=tmp, IDENTITY_FIRST_PILOT="1")
    from flask import Flask

    from identity import core, sightings
    from identity.sighting_api import bp

    app = Flask(__name__)
    app.register_blueprint(bp)
    client = app.test_client()
    from visions_feed.db import initialize

    initialize()
    (root / "preview").touch()
    with core.connect() as db:
        db.executescript(sightings.SCHEMA)
        db.execute("ATTACH DATABASE ? AS feed", (str(root / "feed.sqlite"),))
        vec = np.eye(2, dtype="<f4")
        for i, name in enumerate(("known", "other")):
            db.execute(
                "INSERT INTO refs VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    name,
                    name,
                    name,
                    "enroll",
                    "accepted",
                    None,
                    "unused",
                    name,
                    vec[i].tobytes(),
                    1,
                ),
            )
        db.execute("INSERT INTO versions VALUES(1,1,.4,.4,'m','p','q','{}')")
        db.executemany("INSERT INTO membership VALUES(1,?)", [("known",), ("other",)])

        def segment(oid, cam, seq, at, observations):
            db.execute(
                """INSERT INTO feed.occurrences(occurrence_id,run_id,source_id,asset_id,asset_sha256,session_id,sequence,cycle,input_path,media_path,scheduled_at,state,frame_count,speed,duration_s,synthetic)
                VALUES(?,'test',?,'same','same',?,?,0,'source','preview',?,'complete',20,1,2,0)""",
                (oid, cam, "rtsp-" + cam, seq, at),
            )
            for index, person, score, box in observations:
                did = f"{oid}-{index}-{box[0]}"
                embedding = (
                    vec[0] if person == "known" else np.array([0.72, 0.69], dtype="<f4")
                ).tobytes()
                state = "unknown" if person == "unknown" else "matched"
                db.execute(
                    "INSERT OR IGNORE INTO feed.frames VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        oid,
                        index,
                        index,
                        1,
                        10,
                        index / 10,
                        320,
                        200,
                        "1970-01-01",
                        0,
                        1,
                        "manifest",
                        "{}",
                    ),
                )
                db.execute(
                    "INSERT INTO indexed VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        did,
                        oid,
                        index,
                        cam,
                        at,
                        at,
                        index / 10,
                        "crop",
                        json.dumps(box),
                        "ready",
                        None,
                        embedding,
                        0,
                        0,
                        0,
                    ),
                )
                db.execute(
                    "INSERT INTO observation_features VALUES(?,'m','p','q')", (did,)
                )
                db.execute(
                    "INSERT INTO decisions VALUES(?,1,'known',?,?,.4,NULL)",
                    (did, state, score),
                )

        left = [10, 20, 40, 50]
        right = [200, 10, 40, 50]
        segment(
            "o1",
            "A",
            0,
            100,
            [
                (17, "known", 1, left),
                (17, "unknown", 0.3, right),
                (18, "known", 1, left),
            ],
        )
        segment("o2", "A", 1, 102, [(0, "known", 1, left), (15, "tie", 0.72, right)])
        segment("o3", "A", 2, 120, [(1, "known", 1, left)])
        segment("o4", "B", 0, 120, [(1, "known", 1, left)])
        while sightings.advance(db):
            pass
        assert not sightings.advance(db)
        assert db.execute("select count(*) from sightings").fetchone()[0] == 3
        assert (
            db.execute(
                "select count(*) from sighting_members where reason='ambiguous candidates'"
            ).fetchone()[0]
            == 1
        )
        from identity.evaluate_sightings import evaluate

        db.execute(
            "UPDATE feed.occurrences SET asset_sha256='different-session' WHERE occurrence_id='o4'"
        )
        labels = [
            dict(
                split="calibration",
                session="trial-a",
                person="known",
                detections=["o1-17-10"],
            ),
            dict(
                split="evaluation",
                session="trial-b",
                person="known",
                detections=["o4-1-10"],
            ),
            dict(split="evaluation", session="trial-b", person="absent", detections=[]),
        ]
        report = evaluate(db, labels, 1)
        assert (
            report["results"][0]["correct"] == 1
            and report["results"][-1]["enrolled_missed_or_withheld"] == 1
        )
        endpoint = "/api/operator/identified"
        params = "?from=90&to=150"
        assert client.get(endpoint + "/people" + params).json["items"] == []
        db.execute("update sightings set reviewed_by='labeled fixture',reviewed_at=130")
        db.commit()
        people = client.get(endpoint + "/people" + params).json["items"]
        assert len(people) == 1 and people[0]["appearances"] == 3
        visits = client.get(
            endpoint + "/appearances" + params + "&person=known&camera=A"
        ).json["items"]
        assert len(visits) == 2
        path = endpoint + "/appearances/" + visits[-1]["id"]
        detail = client.get(path).json
        assert (
            detail["reference_id"] == "known"
            and detail["evidence"]["detection_id"] == "o1-17-10"
        )
        db.execute("INSERT INTO versions VALUES(2,2,.5,.5,'m','p','q','{}')")
        db.commit()
        assert client.get(path).json == detail
        spans = client.get(path + "/playback").json["spans"]
        assert [s["occurrence_id"] for s in spans] == ["o1", "o2"]
        assert [b["frame_index"] for s in spans for b in s["boxes"]] == [17, 18, 0]
        assert all(json.loads(b["bbox"]) == left for s in spans for b in s["boxes"])
        db.execute(
            "update feed.occurrences set state='expired' where occurrence_id='o1'"
        )
        db.commit()
        assert client.get(path + "/playback").json["availability"] == "partial"
        db.execute(
            "update feed.occurrences set state='expired' where occurrence_id='o2'"
        )
        db.commit()
        assert client.get(path + "/playback").status_code == 410
        assert client.get(path).json["evidence"] is None
        print(
            "PASS: reviewed people, distinct appearances, exact evidence, crossing-camera playback, ties, retries, frozen gallery and expiry"
        )
