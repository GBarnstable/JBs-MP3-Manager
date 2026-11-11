"""Distinguish unreviewed AI assignments from categories confirmed by the user."""

import json

REVIEW_CATEGORY = "Review after AI tag"
REVIEW_DESCRIPTION = "Songs categorized by AI suggestions that still need your own category review."


def initialize(library):
    """Recover known pre-0.5.2 AI assignments once, without altering memberships.

    Older builds did not record manual reaffirmations. Applied audit reports are
    the available evidence; absent or incomplete history cannot establish origin.
    """
    if library.setting("category_origins_initialized", False):
        return
    categories = {c["id"]: c["name"] for c in library.categories()}
    by_name = {name.casefold(): cid for cid, name in categories.items()}
    existing = {(r[0], r[1]) for r in library.db.execute("SELECT song_id,category_id FROM memberships")}
    recovered = set()
    for path in (library.data / "reports").glob("category-suggestions-*.json"):
        try:
            if path.stat().st_size > 32 * 1024 * 1024:
                continue
            report = json.loads(path.read_text("utf-8-sig"))
            if not isinstance(report, dict) or report.get("state") != "applied":
                continue
            if not report.get("added"):
                continue
            skipped = {e["index"] for e in [*report.get("errors", []), *report.get("skipped", [])]}
            for entry in report.get("approved_suggestions", []):
                if entry["index"] in skipped or entry["status"] != "Ready":
                    continue
                category_id = entry.get("category_id")
                if category_id is None:
                    category_id = by_name.get(entry["category"].casefold())
                pair = entry["song_id"], category_id
                if pair in existing:
                    recovered.add(pair)
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            # Historical reports are data, and must not prevent opening a library.
            continue
    with library.db:
        library.db.executemany("INSERT OR IGNORE INTO ai_memberships VALUES (?, ?)", sorted(recovered))
        library.db.execute("INSERT OR REPLACE INTO settings VALUES ('category_origins_initialized', 'true')")


def manually_categorized(library):
    return {
        row[0]
        for row in library.db.execute(
            "SELECT DISTINCT m.song_id FROM memberships m "
            "JOIN categories c ON c.id=m.category_id "
            "LEFT JOIN ai_memberships a ON a.song_id=m.song_id AND a.category_id=m.category_id "
            "WHERE a.song_id IS NULL AND c.name != ? COLLATE NOCASE",
            (REVIEW_CATEGORY,),
        )
    }


def merge_memberships(library, keeper, sources):
    """Caller owns the transaction; a manual confirmation wins over AI origin."""
    manual, ai = set(), set()
    for song_id in {keeper, *sources}:
        for row in library.db.execute(
            "SELECT m.category_id, a.song_id FROM memberships m LEFT JOIN ai_memberships a "
            "ON a.song_id=m.song_id AND a.category_id=m.category_id WHERE m.song_id=?",
            (song_id,),
        ):
            (ai if row[1] else manual).add(row[0])
    library.db.executemany(
        "INSERT OR IGNORE INTO memberships VALUES (?, ?)", [(keeper, c) for c in manual | ai]
    )
    library.db.executemany(
        "INSERT OR IGNORE INTO ai_memberships VALUES (?, ?)", [(keeper, c) for c in ai - manual]
    )
    library.db.executemany(
        "DELETE FROM ai_memberships WHERE song_id=? AND category_id=?", [(keeper, c) for c in manual]
    )
