"""A keyset-paged projection of the immutable reference and membership records."""

import base64
import json
from pathlib import Path
from tempfile import NamedTemporaryFile

from PIL import Image, ImageOps

from . import core


def initialize(db):
    # Enrollment and publication also run outside the API process. Triggers keep their changes atomic.
    db.execute("BEGIN IMMEDIATE")
    try:
        if db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='cards_after_membership'"
        ).fetchone():
            db.commit()
            return
        db.execute("""CREATE TABLE gallery_cards(scope INTEGER NOT NULL, filter TEXT NOT NULL,
            person_id TEXT NOT NULL, id_key TEXT NOT NULL, name_key TEXT NOT NULL, display_name TEXT NOT NULL,
            chosen TEXT NOT NULL, status TEXT NOT NULL, reference_count INTEGER NOT NULL,
            PRIMARY KEY(scope,filter,person_id)) WITHOUT ROWID""")
        db.execute(
            "CREATE INDEX cards_id ON gallery_cards(scope,filter,id_key,person_id)"
        )
        db.execute(
            "CREATE INDEX cards_name ON gallery_cards(scope,filter,name_key,person_id)"
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS refs_person ON refs(person_id,reference_id)"
        )
        db.execute(
            "CREATE TABLE gallery_counts(scope INTEGER PRIMARY KEY, people INTEGER NOT NULL DEFAULT 0, portraits INTEGER NOT NULL DEFAULT 0)"
        )
        columns = "(scope,filter,person_id,id_key,name_key,display_name,chosen,status,reference_count)"
        filters = "CROSS JOIN (SELECT 0 AS by_status UNION ALL SELECT 1)"
        group = "CASE WHEN by_status=0 THEN '' ELSE r.status END"
        pick = "(CASE WHEN r.status='accepted' THEN '0' ELSE '1' END || r.reference_id)"
        for table, trigger, scope, source in [
            ("refs", "cards_after_ref", "0", "WHERE r.reference_id=NEW.reference_id"),
            (
                "membership",
                "cards_after_membership",
                "NEW.version",
                "WHERE r.reference_id=NEW.reference_id",
            ),
        ]:
            db.execute(f"""CREATE TRIGGER {trigger} AFTER INSERT ON {table} BEGIN
                INSERT INTO gallery_counts VALUES({scope}, CASE WHEN EXISTS(SELECT 1 FROM gallery_cards
                  WHERE scope={scope} AND filter='' AND person_id=(SELECT person_id FROM refs WHERE reference_id=NEW.reference_id)) THEN 0 ELSE 1 END,1)
                ON CONFLICT(scope) DO UPDATE SET people=people+excluded.people,portraits=portraits+1;
                INSERT INTO gallery_cards {columns}
                SELECT {scope},{group},r.person_id,lower(r.person_id),lower(r.display_name),r.display_name,{pick},r.status,1
                FROM refs r {filters} {source}
                ON CONFLICT(scope,filter,person_id) DO UPDATE SET reference_count=reference_count+1,
                  name_key=CASE WHEN excluded.chosen<chosen THEN excluded.name_key ELSE name_key END,
                  display_name=CASE WHEN excluded.chosen<chosen THEN excluded.display_name ELSE display_name END,
                  status=CASE WHEN excluded.chosen<chosen THEN excluded.status ELSE status END,chosen=min(chosen,excluded.chosen);
                END""")
        for scope, membership in [
            ("0", ""),
            ("m.version", "JOIN membership m USING(reference_id)"),
        ]:
            db.execute(f"""INSERT INTO gallery_cards {columns}
                SELECT g.scope,g.kind,r.person_id,lower(r.person_id),lower(r.display_name),r.display_name,
                       g.chosen,r.status,g.n FROM (SELECT {scope} AS scope,{group} AS kind,r.person_id,
                       min({pick}) AS chosen,count(*) n FROM refs r {membership} {filters}
                       GROUP BY scope,kind,r.person_id) g JOIN refs r ON r.reference_id=substr(g.chosen,2)""")
        db.execute("""INSERT INTO gallery_counts SELECT scope,count(*),sum(reference_count)
                      FROM gallery_cards WHERE filter='' GROUP BY scope""")
        db.commit()
    except BaseException:
        db.rollback()
        raise


def versions(db, selected=None):
    rows = db.execute(
        """SELECT v.*,c.people AS people_count,c.portraits AS reference_count FROM versions v
      JOIN gallery_counts c ON c.scope=v.version WHERE v.version IN (SELECT version FROM versions ORDER BY version DESC LIMIT 100)
      OR v.version=? ORDER BY v.version DESC""",
        (selected,),
    )
    return [
        dict(
            r,
            created_at=core.utc(r["created_at"]),
            calibration=json.loads(r["calibration"]),
        )
        for r in rows
    ]


def people(db, scope, query, status, after, limit):
    if (
        status not in ("", "accepted", "rejected")
        or not 1 <= limit <= 48
        or len(query) > 120
    ):
        raise ValueError("Invalid gallery filter or page size")
    prefix = query.lower().strip()
    if prefix.startswith("شرکت‌کنندهٔ"):
        prefix = ("participant " + prefix.removeprefix("شرکت‌کنندهٔ").strip()).rstrip()
    phase, last, last_person = 0, "", ""
    if after:
        try:
            if len(after) > 1600:
                raise ValueError
            vals = json.loads(base64.urlsafe_b64decode(after).decode())
            if (
                not isinstance(vals, list)
                or vals[:3] != [scope, prefix, status]
                or vals[3] not in (0, 1)
                or not all(isinstance(v, str) for v in vals[4:])
                or len(vals) != 6
            ):
                raise ValueError
            phase, last, last_person = vals[3:]
        except (ValueError, TypeError, IndexError, UnicodeError) as exc:
            raise ValueError("Invalid gallery cursor") from exc
    out = []
    for mode in range(phase, 2 if prefix else 1):
        col = ("id_key", "name_key")[mode]
        args = [
            scope,
            status,
            max(last, prefix),
            prefix + chr(0x10FFFF),
            max(last, prefix),
            last_person,
        ]
        exclude = ""
        if mode:
            exclude = "AND (id_key<? OR id_key>=?)"
            args.extend((prefix, prefix + chr(0x10FFFF)))
        rows = db.execute(
            f"""SELECT person_id,display_name,status,reference_count,substr(chosen,2) ref,{col} sort_key
            FROM gallery_cards WHERE scope=? AND filter=? AND {col}>=? AND {col}<?
            AND ({col},person_id)>(?,?) {exclude} ORDER BY {col},person_id LIMIT ?""",
            (*args, limit + 1 - len(out)),
        )
        out.extend((mode, dict(r)) for r in rows)
        if len(out) > limit:
            break
        last, last_person = "", ""
    next_cursor = None
    if len(out) > limit:
        mode, row = out[limit - 1]
        next_cursor = base64.urlsafe_b64encode(
            json.dumps(
                [scope, prefix, status, mode, row["sort_key"], row["person_id"]],
                ensure_ascii=False,
            ).encode()
        ).decode()
    items = [
        dict(
            person_id=r["person_id"],
            display_name=r["display_name"],
            status=r["status"],
            reference_count=r["reference_count"],
            thumbnail_url="/api/identity/references/" + r["ref"] + "/thumbnail",
        )
        for _, r in out[:limit]
    ]
    return dict(people=items, next_cursor=next_cursor)


def references(db, scope, person, after="", limit=32):
    restriction = (
        "AND EXISTS(SELECT 1 FROM membership WHERE version=? AND reference_id=r.reference_id)"
        if scope
        else "AND NOT EXISTS(SELECT 1 FROM retired_draft_refs h WHERE h.reference_id=r.reference_id)"
    )
    args = (person, after, scope) if scope else (person, after)
    rows = db.execute(
        f"""SELECT r.*,f.model,f.preprocessing,f.quality_policy FROM refs r JOIN reference_features f USING(reference_id)
                  WHERE r.person_id=? AND r.reference_id>? {restriction} ORDER BY r.reference_id LIMIT ?""",
        (*args, limit + 1),
    ).fetchall()
    return dict(
        references=[core.public_ref(r) for r in rows[:limit]],
        next_cursor=rows[limit - 1]["reference_id"] if len(rows) > limit else None,
    )


def thumbnail(ref):
    with core.connect() as db:
        row = db.execute(
            "SELECT portrait_path FROM refs WHERE reference_id=?", (ref,)
        ).fetchone()
    if not row:
        raise KeyError("Reference not found")
    original = core.checked_path(row[0])
    directory = core.ROOT / "reference-thumbnails"
    directory.mkdir(mode=0o700, exist_ok=True)
    target = directory / (ref + ".jpg")
    if not target.is_file():
        with Image.open(original) as im:
            thumb = ImageOps.exif_transpose(im).convert("RGB")
            thumb.thumbnail((192, 192), Image.Resampling.LANCZOS)
            with NamedTemporaryFile(dir=directory, delete=False) as tmp:
                path = Path(tmp.name)
                try:
                    thumb.save(tmp, format="JPEG", quality=80)
                    tmp.flush()
                    path.replace(target)
                finally:
                    path.unlink(missing_ok=True)
    return str(target.relative_to(core.ROOT))
