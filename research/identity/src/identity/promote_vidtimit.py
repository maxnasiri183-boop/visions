"""Promote a verified private candidate atomically while preserving every historical gallery version."""

import argparse
from contextlib import closing
import fcntl
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import time

from .import_vidtimit import digest
from .vidtimit_candidate import PREFIX


def read(path):
    return json.loads(path.read_text())


def link_verified(source, target, expected):
    if not source.is_file() or digest(source) != expected:
        raise ValueError(f"Candidate file differs: {source.name}")
    if target.exists():
        if digest(target) != expected:
            raise ValueError(f"Different file already exists: {target.name}")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, target)
    except FileExistsError:
        if digest(target) != expected:
            raise ValueError(f"Different file already exists: {target.name}")
    except OSError:
        with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as sink:
            temporary = Path(sink.name)
            try:
                with source.open("rb") as stream:
                    shutil.copyfileobj(stream, sink)
                sink.flush()
                os.link(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)


def target_path(root, relative):
    path = (root / relative).resolve()
    if path == root or not path.is_relative_to(root):
        raise ValueError("Invalid candidate-relative path")
    return path


def merge_playlist(existing, additional):
    ids = {(a["source_id"], a["asset_id"]): a for a in existing}
    checksums = {a.get("sha256") for a in existing}
    result = list(existing)
    for row in additional:
        key = row["source_id"], row["asset_id"]
        if key in ids and ids[key] != row:
            raise ValueError("Conflicting preexisting catalog asset")
        if key in ids or row["sha256"] in checksums:
            continue
        ids[key] = row
        checksums.add(row["sha256"])
        result.append(row)
    return result


def retire_predecessors(db, predecessors):
    db.execute(
        "CREATE TABLE IF NOT EXISTS main.retired_draft_refs(reference_id TEXT PRIMARY KEY REFERENCES refs(reference_id))"
    )
    db.executemany(
        "INSERT INTO main.retired_draft_refs VALUES(?)",
        [(r["reference_id"],) for r in predecessors],
    )
    if not db.execute(
        "SELECT 1 FROM main.sqlite_master WHERE name='gallery_cards'"
    ).fetchone():
        return
    # An older rejected photo can still belong to one of the three people. Rebuild
    # only their draft cards, leaving historical version projections untouched.
    for person in {row["person_id"] for row in predecessors}:
        db.execute(
            "DELETE FROM main.gallery_cards WHERE scope=0 AND person_id=?", (person,)
        )
        db.execute(
            """
            INSERT INTO main.gallery_cards(scope,filter,person_id,id_key,name_key,display_name,chosen,status,reference_count)
            SELECT 0,kind,r.person_id,lower(r.person_id),lower(r.display_name),r.display_name,chosen,r.status,n FROM (
              SELECT CASE WHEN by_status=1 THEN r.status ELSE '' END AS kind,
                min(CASE WHEN status='accepted' THEN '0' ELSE '1' END || r.reference_id) chosen,count(*) n
              FROM main.refs r CROSS JOIN (SELECT 0 AS by_status UNION ALL SELECT 1)
              WHERE r.person_id=? AND NOT EXISTS (SELECT 1 FROM main.retired_draft_refs h WHERE h.reference_id=r.reference_id)
              GROUP BY kind
            ) g JOIN main.refs r ON r.reference_id=substr(g.chosen,2)
        """,
            (person,),
        )
    db.execute("""UPDATE main.gallery_counts SET (people,portraits)=(
        SELECT count(*),coalesce(sum(reference_count),0) FROM main.gallery_cards WHERE scope=0 AND filter=''
    ) WHERE scope=0""")


def install_version(target, candidate, config, expected):
    with closing(
        sqlite3.connect(
            (target / "identity.sqlite").as_uri() + "?mode=rw", uri=True, timeout=30
        )
    ) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute(
            "ATTACH DATABASE ? AS staged",
            ((candidate / "identity.sqlite").as_uri() + "?mode=ro",),
        )
        staged = db.execute(
            "SELECT * FROM staged.versions ORDER BY version DESC LIMIT 1"
        ).fetchone()
        if staged is None or json.loads(staged["calibration"]) != config:
            raise ValueError(
                "Staged immutable gallery disagrees with its frozen policy"
            )
        rows = db.execute(
            """SELECT r.*,f.face_path,f.model,f.preprocessing,f.quality_policy FROM staged.membership m
                JOIN staged.refs r USING(reference_id) JOIN staged.reference_features f USING(reference_id)
                WHERE m.version=?""",
            (staged["version"],),
        ).fetchall()
        from .core import gallery_hash

        if (
            len(rows) != 28
            or len({r["person_id"] for r in rows}) != 28
            or gallery_hash(rows) != config["gallery_hash"]
            or any(
                (r["model"], r["preprocessing"], r["quality_policy"])
                != (staged["model"], staged["preprocessing"], staged["policy"])
                for r in rows
            )
        ):
            raise ValueError("Staged references and frozen membership differ")
        with db:
            db.execute("BEGIN IMMEDIATE")
            active = db.execute(
                "SELECT * FROM main.versions ORDER BY version DESC LIMIT 1"
            ).fetchone()
            if active and json.loads(active["calibration"]) == config:
                installed = db.execute(
                    "SELECT * FROM main.refs JOIN main.membership USING(reference_id) WHERE version=?",
                    (active["version"],),
                ).fetchall()
                if (
                    len(installed) != len(rows)
                    or gallery_hash(installed) != config["gallery_hash"]
                ):
                    raise ValueError(
                        "Installed membership differs from its immutable candidate"
                    )
                return active["version"]
            if not active or active["version"] != expected:
                raise ValueError("The active gallery changed before promotion")
            aliases = read(candidate / "private-alias-crosswalk.json")
            by_key = {row["person_id"]: row for row in rows}
            old = db.execute(
                """SELECT r.* FROM main.membership m JOIN main.refs r USING(reference_id)
                    WHERE m.version=? AND r.status='accepted'""",
                (expected,),
            ).fetchall()
            pilot_people = {
                person
                for row in read(target / "research/enrollment.json")
                for person in row["people"]
            }
            if {r["person_id"] for r in old} != pilot_people or len(old) != 3:
                raise ValueError("Expected the three immutable pilot references")
            for predecessor in old:
                successor = by_key.get(aliases[predecessor["person_id"]]["person_id"])
                if (
                    successor is None
                    or successor["sha256"] != predecessor["sha256"]
                    or bytes(successor["embedding"]) != bytes(predecessor["embedding"])
                ):
                    raise ValueError(
                        "Pilot and successor must use the identical original photograph and embedding"
                    )
            old_ids = {r["reference_id"] for r in old}
            existing = db.execute(
                "SELECT * FROM main.refs WHERE status='accepted'"
            ).fetchall()
            keys = {(r["person_id"], r["sha256"], r["session_id"]) for r in rows}
            if any(
                r["reference_id"] not in old_ids
                and (r["person_id"], r["sha256"], r["session_id"]) not in keys
                for r in existing
            ):
                raise ValueError(
                    "Unrelated accepted references require a combined-gallery calibration"
                )
            retire_predecessors(db, old)
            members = []
            for row in rows:
                found = db.execute(
                    """SELECT * FROM main.refs JOIN main.reference_features USING(reference_id)
                        WHERE person_id=? AND sha256=? AND session_id=?""",
                    (row["person_id"], row["sha256"], row["session_id"]),
                ).fetchone()
                if found:
                    if (
                        found["status"] != "accepted"
                        or bytes(found["embedding"]) != bytes(row["embedding"])
                        or (
                            found["model"],
                            found["preprocessing"],
                            found["quality_policy"],
                        )
                        != (row["model"], row["preprocessing"], row["quality_policy"])
                    ):
                        raise ValueError(
                            "Existing reference has incompatible status, model or embedding"
                        )
                    members.append(found["reference_id"])
                    continue
                for field in ("portrait_path", "face_path"):
                    source = target_path(candidate, row[field])
                    link_verified(
                        source, target_path(target, row[field]), digest(source)
                    )
                db.execute(
                    "INSERT INTO main.refs SELECT * FROM staged.refs WHERE reference_id=?",
                    (row["reference_id"],),
                )
                db.execute(
                    "INSERT INTO main.reference_features SELECT * FROM staged.reference_features WHERE reference_id=?",
                    (row["reference_id"],),
                )
                members.append(row["reference_id"])
            cursor = db.execute(
                """INSERT INTO main.versions(created_at,threshold,search_threshold,model,preprocessing,policy,calibration)
                    SELECT ?,threshold,search_threshold,model,preprocessing,policy,calibration FROM staged.versions WHERE version=?""",
                (time.time(), staged["version"]),
            )
            version = cursor.lastrowid
            db.executemany(
                "INSERT INTO main.membership VALUES(?,?)",
                [(version, member) for member in members],
            )
            return version


def promote(candidate, target, expected):
    if target == candidate or not (target / "identity.sqlite").is_file():
        raise ValueError(
            "Choose an initialized operator target separate from the candidate"
        )
    config = read(candidate / "frozen-calibration.json")
    summary = read(candidate / "quality-summary.json")
    if (
        summary["gallery_identities"] != 28
        or summary["audit"]["cross_role_exact_or_near_duplicates"]
    ):
        raise ValueError("Missing passing independent candidate audit")
    for role, checksum in config["split_hashes"].items():
        if digest(candidate / (role + ".json")) != checksum:
            raise ValueError("Candidate manifest differs from the freeze")
    if config["alias_manifest_sha256"] != digest(
        candidate / "private-alias-crosswalk.json"
    ):
        raise ValueError("Private verification crosswalk differs from the freeze")
    stream = {r["sha256"]: r for r in read(candidate / "stream.json")}
    entries = read(candidate / "catalog.candidate.json")
    aliases = read(candidate / "private-alias-crosswalk.json")
    if len(entries) != len(stream) or {r["sha256"] for r in entries} != set(stream):
        raise ValueError("Catalog and frozen stream manifest differ")
    path = target / "catalog.json"
    with (target / ".vidtimit43-promote.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        existing = read(path)
        joined = merge_playlist(existing, entries)
        for row in entries:
            sha = row["sha256"]
            if (
                row["source_id"] != "studio"
                or row["asset_id"] != "vt-" + sha[:24]
                or row["session_id"]
                != aliases[stream[sha]["person_id"]]["video_session"]
                or row["path"] != str(PREFIX / "media" / (sha + ".mkv"))
            ):
                raise ValueError("Unexpected research catalog path or source")
            if row in joined:
                link_verified(
                    target_path(candidate, stream[sha]["path"]),
                    target_path(target, row["path"]),
                    sha,
                )
        for name in (
            "VidTIMIT-original-notice.html",
            "vidtimit_documentation.pdf",
            "CREDITS.txt",
        ):
            notice = candidate / "archives" / name
            link_verified(notice, target / PREFIX / "license" / name, digest(notice))
        version = install_version(target, candidate, config, expected)
        # Re-read the current catalog so independent additions survive an interrupted promotion.
        current = read(path)
        joined = merge_playlist(current, entries)
        if joined != current:
            with tempfile.NamedTemporaryFile(
                dir=target, suffix=".json", mode="w", delete=False
            ) as handle:
                json.dump(joined, handle, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
                tmp = Path(handle.name)
            tmp.replace(path)
        if config != read(candidate / "frozen-calibration.json"):
            raise ValueError("Frozen candidate changed during promotion")
        return {
            "version": version,
            "gallery_identities": summary["gallery_identities"],
            "catalog_added": len(joined) - len(current),
            "catalog_total": len(joined),
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--expect-version", type=int, required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            promote(
                args.candidate.resolve(), args.target.resolve(), args.expect_version
            )
        )
    )
