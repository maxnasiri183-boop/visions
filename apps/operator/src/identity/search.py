"""Exact cosine search after source and UTC filters, grouped into reviewable appearances."""

from datetime import datetime
import json
import numpy as np

from . import core as c


def stamp(value):
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise ValueError("Expected ISO-8601 date and time with UTC offset")
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("Specify a UTC offset for each time")
    return dt.timestamp()


def overlap(a, b):
    x, y, w, h = a
    u, v, width, height = b
    area = max(0, min(x + w, u + width) - max(x, u)) * max(
        0, min(y + h, v + height) - max(y, v)
    )
    return area / max(1, w * h + width * height - area)


def scored_rows(cursor, vector):
    query = np.frombuffer(vector, "<f4")
    while chunk := cursor.fetchmany(512):
        matrix = np.frombuffer(
            b"".join(row["embedding"] for row in chunk), "<f4"
        ).reshape(len(chunk), 128)
        values = np.clip(np.einsum("ij,j->i", matrix, query), -1.0, 1.0)
        yield from zip(chunk, map(float, values))


def search(db, vector, cutoff, body, occurrences=None):
    start, end = stamp(body.get("from_utc")), stamp(body.get("to_utc"))
    if start is not None and end is not None and start >= end:
        raise ValueError("End time must follow start time")
    limit = body.get("limit", 20)
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("Limit must be between 1 and 100 appearances")
    filters, params = (
        ["state='ready'", "metadata.model=?", "metadata.preprocessing=?"],
        [c.MODEL, c.PRE],
    )
    attached = {r[1] for r in db.execute("PRAGMA database_list")}
    if "feed" not in attached and (c.ROOT / "feed.sqlite").is_file():
        db.execute("ATTACH DATABASE ? AS feed", (str(c.ROOT / "feed.sqlite"),))
        attached.add("feed")
    if "feed" in attached:
        filters.append(
            "EXISTS(SELECT 1 FROM feed.occurrences live WHERE live.occurrence_id=indexed.occurrence_id AND live.state!='expired')"
        )
    for field, value in (
        ("source_id=", body.get("source_id")),
        ("ingested_at>=", start),
        ("ingested_at<", end),
    ):
        if value not in (None, ""):
            filters.append(field + "?")
            params.append(value)
    if occurrences is not None:
        filters.append("occurrence_id IN (" + ",".join("?" for _ in occurrences) + ")")
        params.extend(occurrences)
    query = (
        "SELECT indexed.* FROM indexed JOIN observation_features metadata USING(detection_id) WHERE "
        + " AND ".join(filters)
        + " ORDER BY occurrence_id,frame_index,detection_id"
    )
    best = []
    groups = []
    occurrence = None
    candidates = 0

    def finish():
        nonlocal best
        for g in groups:
            del g["box"]
        best = sorted(
            [*best, *groups],
            key=lambda g: (-g["score"], g["occurrence_id"], g["first_s"]),
        )[:limit]
        groups.clear()

    for row, score in scored_rows(db.execute(query, params), vector):
        candidates += 1
        if row["occurrence_id"] != occurrence:
            finish()
            occurrence = row["occurrence_id"]
        if score < cutoff:
            continue
        thumb = "/api/identity/observations/" + row["detection_id"] + "/thumbnail"
        obs = dict(
            detection_id=row["detection_id"],
            frame_index=row["frame_index"],
            score=score,
            media_pts_s=row["media_pts_s"],
            thumbnail_url=thumb,
            occurrence_id=occurrence,
            observed_utc=c.utc(row["ingested_at"]),
        )
        box = json.loads(row["bbox"])
        eligible = [
            (overlap(box, g["box"]), g)
            for g in groups
            if 0 < row["frame_index"] - g["observations"][-1]["frame_index"] <= 3
        ]
        iou, group = max(eligible, key=lambda pair: pair[0], default=(0, None))
        if iou < 0.15:
            group = dict(
                score=score,
                source_id=row["source_id"],
                occurrence_id=occurrence,
                first_s=row["media_pts_s"],
                thumbnail_url=thumb,
                observations=[],
                observed_utc=obs["observed_utc"],
            )
            groups.append(group)
        group.update(last_s=row["media_pts_s"], box=box)
        if score > group["score"]:
            group.update(score=score, thumbnail_url=thumb)
        group["observations"].append(obs)
    finish()
    if best:
        history = {}
        if "feed" in attached:
            ids = sorted({g["occurrence_id"] for g in best})
            placeholders = ",".join("?" for _ in ids)
            for r in db.execute(
                f"""SELECT u.occurrence_id,u.asset_id,u.cycle,u.sequence,u.run_id,
                EXISTS(SELECT 1 FROM feed.occurrences prior WHERE prior.run_id=u.run_id
                       AND prior.source_id=u.source_id AND prior.sequence<u.sequence AND prior.asset_sha256=u.asset_sha256)
                repeated FROM feed.occurrences u WHERE u.state!='expired' AND u.occurrence_id IN ({placeholders})""",
                ids,
            ):
                history[r["occurrence_id"]] = dict(
                    asset_id=r["asset_id"],
                    cycle=r["cycle"],
                    sequence=r["sequence"],
                    run_id=r["run_id"],
                    repeated=bool(r["repeated"]),
                )
            # An airing can expire while the exact search scans retained embeddings.
            best = [g for g in best if g["occurrence_id"] in history]
        for g in best:
            g.update(
                history.get(
                    g["occurrence_id"],
                    dict(
                        asset_id=None,
                        cycle=None,
                        sequence=None,
                        run_id=None,
                        repeated=None,
                    ),
                )
            )
    return dict(
        groups=best,
        candidate_count=candidates,
        match_threshold=cutoff,
        model=c.MODEL,
        preprocessing=c.PRE,
        index_version="sqlite-exact-cosine-f32-v1",
    )
