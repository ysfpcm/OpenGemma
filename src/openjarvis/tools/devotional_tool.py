"""Local devotional content store for the spiritual-readiness agent.

The store deliberately keeps source scripture separate from generated
reflection.  The agent may compose a meditation from the returned record, but
it must not invent or paraphrase a verse when the source text is unavailable.
Entries and preferences live in a small SQLite database under the OpenJarvis
config directory so users can add their own devotional tradition and material
without changing application code.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Optional

from openjarvis.core.config import DEFAULT_CONFIG_DIR
from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec


DEFAULT_DEVOTIONAL_DB_PATH = str(
    DEFAULT_CONFIG_DIR / "devotional.db"
)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS devotionals (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    slug                TEXT NOT NULL UNIQUE,
    tradition           TEXT NOT NULL DEFAULT 'christian',
    translation         TEXT NOT NULL DEFAULT 'KJV',
    scripture_reference TEXT NOT NULL,
    scripture_text      TEXT NOT NULL,
    theme               TEXT NOT NULL DEFAULT '',
    reflection_prompt   TEXT NOT NULL DEFAULT '',
    prayer_prompt       TEXT NOT NULL DEFAULT '',
    practice            TEXT NOT NULL DEFAULT '',
    weekday             INTEGER,
    tags_json           TEXT NOT NULL DEFAULT '[]',
    source              TEXT NOT NULL DEFAULT 'user',
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS devotional_preferences (
    id                  INTEGER PRIMARY KEY CHECK (id = 1),
    tradition           TEXT NOT NULL DEFAULT 'christian',
    translation         TEXT NOT NULL DEFAULT 'KJV',
    preferred_time      TEXT NOT NULL DEFAULT '05:00',
    worship_focus       TEXT NOT NULL DEFAULT '',
    prayer_topics_json  TEXT NOT NULL DEFAULT '[]',
    tone                TEXT NOT NULL DEFAULT 'gentle',
    updated_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS devotional_schema_meta (
    key                 TEXT PRIMARY KEY,
    value               TEXT NOT NULL
);
"""


_DEFAULT_PREFERENCES: Dict[str, Any] = {
    "tradition": "christian",
    "translation": "KJV",
    "preferred_time": "05:00",
    "worship_focus": "Prepare my heart for worship and faithful action.",
    "prayer_topics": [],
    "tone": "gentle",
}


_DEFAULT_DEVOTIONALS = (
    {
        "slug": "morning-prayer",
        "weekday": 0,
        "reference": "Psalm 5:3",
        "text": "My voice shalt thou hear in the morning, O LORD; in the morning will I direct my prayer unto thee, and will look up.",
        "theme": "Beginning the day with prayer",
        "reflection": "What would it look like to turn toward God before the day's demands set the direction for you?",
        "prayer": "Ask for a steady heart, an attentive mind, and a willingness to receive guidance before acting.",
        "practice": "Spend two quiet minutes naming what you are grateful for and what you are placing in God's hands.",
    },
    {
        "slug": "new-mercies",
        "weekday": 1,
        "reference": "Lamentations 3:22-23",
        "text": "It is of the LORD'S mercies that we are not consumed, because his compassions fail not. They are new every morning: great is thy faithfulness.",
        "theme": "Receiving a new morning",
        "reflection": "Where do you need to receive mercy rather than carry yesterday's failure into today?",
        "prayer": "Give thanks for the faithfulness that meets you again this morning, and ask for grace to extend that mercy to others.",
        "practice": "Release one regret from yesterday and choose one faithful step for today.",
    },
    {
        "slug": "show-me-the-way",
        "weekday": 2,
        "reference": "Psalm 143:8",
        "text": "Cause me to hear thy lovingkindness in the morning; for in thee do I trust: cause me to know the way wherein I should walk; for I lift up my soul unto thee.",
        "theme": "Asking for direction",
        "reflection": "What decision or responsibility would benefit from being brought into prayer before it is brought into motion?",
        "prayer": "Ask for clarity about the next faithful step, not control over every step after it.",
        "practice": "Write down the single next action that best reflects your values today.",
    },
    {
        "slug": "renewed-strength",
        "weekday": 3,
        "reference": "Isaiah 40:31",
        "text": "But they that wait upon the LORD shall renew their strength; they shall mount up with wings as eagles; they shall run, and not be weary; and they shall walk, and not faint.",
        "theme": "Strength for the work ahead",
        "reflection": "Where are you relying only on your own energy, and what would patient trust look like there?",
        "prayer": "Ask for strength that is steady rather than hurried, and for wisdom to recognize when rest is faithful.",
        "practice": "Take one intentional breath before each major transition in your morning.",
    },
    {
        "slug": "seek-first",
        "weekday": 4,
        "reference": "Matthew 6:33",
        "text": "But seek ye first the kingdom of God, and his righteousness; and all these things shall be added unto you.",
        "theme": "Ordering the day around what matters",
        "reflection": "What would change if your first priority today were faithfulness rather than urgency?",
        "prayer": "Ask for your priorities to be reordered around love, truth, service, and righteousness.",
        "practice": "Choose one task today that serves another person without seeking recognition.",
    },
    {
        "slug": "satisfy-us-early",
        "weekday": 5,
        "reference": "Psalm 90:14",
        "text": "O satisfy us early with thy mercy; that we may rejoice and be glad all our days.",
        "theme": "Joy before accomplishment",
        "reflection": "Can you receive this day as a gift before measuring what you have accomplished in it?",
        "prayer": "Ask for a joy that is rooted in mercy and presence, not in performance.",
        "practice": "Notice and name three ordinary gifts before opening your work or message queue.",
    },
    {
        "slug": "encourage-one-another",
        "weekday": 6,
        "reference": "Hebrews 10:24-25",
        "text": "And let us consider one another to provoke unto love and to good works: Not forsaking the assembling of ourselves together, as the manner of some is; but exhorting one another: and so much the more, as ye see the day approaching.",
        "theme": "Being drawn toward worship and community",
        "reflection": "Who might be strengthened today by your presence, encouragement, or willingness to worship alongside them?",
        "prayer": "Ask for a heart that seeks faithful community and helps others move toward love and good works.",
        "practice": "Make one concrete plan to participate in worship or encourage someone in your faith community.",
    },
)


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "devotional"


def _parse_date(value: str) -> date:
    return date.fromisoformat(value) if value else date.today()


class DevotionalStore:
    """SQLite repository for devotional entries and user preferences."""

    def __init__(self, db_path: str | Path = DEFAULT_DEVOTIONAL_DB_PATH) -> None:
        configured_path = os.environ.get("OPENJARVIS_DEVOTIONAL_DB", str(db_path))
        self.path = Path(configured_path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path))
        conn.row_factory = sqlite3.Row
        conn.executescript(_SCHEMA)
        conn.execute(
            "INSERT OR IGNORE INTO devotional_schema_meta(key, value) VALUES (?, ?)",
            ("schema_version", "1"),
        )
        conn.execute(
            """INSERT OR IGNORE INTO devotional_preferences
               (id, tradition, translation, preferred_time, worship_focus,
                prayer_topics_json, tone, updated_at)
               VALUES (1, ?, ?, ?, ?, ?, ?, ?)""",
            (
                _DEFAULT_PREFERENCES["tradition"],
                _DEFAULT_PREFERENCES["translation"],
                _DEFAULT_PREFERENCES["preferred_time"],
                _DEFAULT_PREFERENCES["worship_focus"],
                json.dumps(_DEFAULT_PREFERENCES["prayer_topics"]),
                _DEFAULT_PREFERENCES["tone"],
                _now_iso(),
            ),
        )
        count = conn.execute("SELECT COUNT(*) FROM devotionals").fetchone()[0]
        if count == 0:
            self._seed_defaults(conn)
        conn.commit()
        return conn

    def _seed_defaults(self, conn: sqlite3.Connection) -> None:
        now = _now_iso()
        for entry in _DEFAULT_DEVOTIONALS:
            conn.execute(
                """INSERT INTO devotionals
                   (slug, tradition, translation, scripture_reference,
                    scripture_text, theme, reflection_prompt, prayer_prompt,
                    practice, weekday, tags_json, source, created_at, updated_at)
                   VALUES (?, 'christian', 'KJV', ?, ?, ?, ?, ?, ?, ?, ?, 'builtin', ?, ?)""",
                (
                    entry["slug"],
                    entry["reference"],
                    entry["text"],
                    entry["theme"],
                    entry["reflection"],
                    entry["prayer"],
                    entry["practice"],
                    entry["weekday"],
                    json.dumps(["morning", "worship"]),
                    now,
                    now,
                ),
            )

    def preferences(self, conn: sqlite3.Connection) -> Dict[str, Any]:
        row = conn.execute(
            "SELECT * FROM devotional_preferences WHERE id = 1"
        ).fetchone()
        if row is None:
            return dict(_DEFAULT_PREFERENCES)
        result = dict(row)
        result["prayer_topics"] = json.loads(result.pop("prayer_topics_json") or "[]")
        return result

    def todays_entry(
        self,
        conn: sqlite3.Connection,
        *,
        on_date: date,
        tradition: str,
        translation: str,
    ) -> Optional[Dict[str, Any]]:
        row = conn.execute(
            """SELECT * FROM devotionals
               WHERE tradition = ? AND translation = ?
                 AND (weekday = ? OR weekday IS NULL)
               ORDER BY CASE WHEN weekday = ? THEN 0 ELSE 1 END,
                        CASE WHEN source = 'user' THEN 0 ELSE 1 END, id
               LIMIT 1""",
            (tradition, translation, on_date.weekday(), on_date.weekday()),
        ).fetchone()
        if row is None:
            row = conn.execute(
                """SELECT * FROM devotionals
                   WHERE tradition = ? AND (weekday = ? OR weekday IS NULL)
                   ORDER BY CASE WHEN weekday = ? THEN 0 ELSE 1 END,
                            CASE WHEN source = 'user' THEN 0 ELSE 1 END, id
                   LIMIT 1""",
                (tradition, on_date.weekday(), on_date.weekday()),
            ).fetchone()
        if row is None:
            row = conn.execute(
                "SELECT * FROM devotionals ORDER BY id LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["tags"] = json.loads(result.pop("tags_json") or "[]")
        return result

    def add_entry(self, conn: sqlite3.Connection, values: Dict[str, Any]) -> Dict[str, Any]:
        now = _now_iso()
        slug = _slugify(str(values.get("slug") or values["scripture_reference"]))
        tags = values.get("tags", [])
        if isinstance(tags, str):
            tags = [tag.strip() for tag in tags.split(",") if tag.strip()]
        conn.execute(
            """INSERT INTO devotionals
               (slug, tradition, translation, scripture_reference,
                scripture_text, theme, reflection_prompt, prayer_prompt,
                practice, weekday, tags_json, source, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                slug,
                values.get("tradition", "christian"),
                values.get("translation", "KJV"),
                values["scripture_reference"],
                values["scripture_text"],
                values.get("theme", ""),
                values.get("reflection_prompt", ""),
                values.get("prayer_prompt", ""),
                values.get("practice", ""),
                values.get("weekday"),
                json.dumps(tags),
                values.get("source", "user"),
                now,
                now,
            ),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM devotionals WHERE slug = ?", (slug,)
        ).fetchone()
        return dict(row) if row is not None else {"slug": slug}

    def update_preferences(
        self, conn: sqlite3.Connection, values: Dict[str, Any]
    ) -> Dict[str, Any]:
        current = self.preferences(conn)
        for key in (
            "tradition",
            "translation",
            "preferred_time",
            "worship_focus",
            "prayer_topics",
            "tone",
        ):
            if key in values and values[key] is not None:
                current[key] = values[key]
        topics = current["prayer_topics"]
        if isinstance(topics, str):
            topics = [topic.strip() for topic in topics.split(",") if topic.strip()]
        conn.execute(
            """UPDATE devotional_preferences SET tradition = ?, translation = ?,
               preferred_time = ?, worship_focus = ?, prayer_topics_json = ?,
               tone = ?, updated_at = ? WHERE id = 1""",
            (
                current["tradition"],
                current["translation"],
                current["preferred_time"],
                current["worship_focus"],
                json.dumps(topics),
                current["tone"],
                _now_iso(),
            ),
        )
        conn.commit()
        current["prayer_topics"] = topics
        return current


@ToolRegistry.register("devotional_content")
class DevotionalContentTool(BaseTool):
    """Read or maintain structured devotional material in local SQLite."""

    tool_id = "devotional_content"

    def __init__(self, *, db_path: str | Path = DEFAULT_DEVOTIONAL_DB_PATH) -> None:
        self._store = DevotionalStore(db_path)

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="devotional_content",
            description=(
                "Read today's structured devotional content and preferences, "
                "or add user-supplied devotional material to the local store. "
                "Never invent scripture text or references."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["read", "add", "set_preferences"],
                        "description": "Operation to perform. Defaults to read.",
                    },
                    "date": {
                        "type": "string",
                        "description": "Optional YYYY-MM-DD date for deterministic reads.",
                    },
                    "scripture_reference": {"type": "string"},
                    "scripture_text": {"type": "string"},
                    "theme": {"type": "string"},
                    "reflection_prompt": {"type": "string"},
                    "prayer_prompt": {"type": "string"},
                    "practice": {"type": "string"},
                    "translation": {"type": "string"},
                    "tradition": {"type": "string"},
                    "weekday": {
                        "type": "integer",
                        "description": "Optional weekday index, Monday=0 through Sunday=6.",
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "preferred_time": {"type": "string"},
                    "worship_focus": {"type": "string"},
                    "prayer_topics": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "tone": {"type": "string"},
                },
                "required": [],
            },
            category="personal",
        )

    def execute(self, **params: Any) -> ToolResult:
        action = str(params.get("action", "read") or "read").lower()
        conn: Optional[sqlite3.Connection] = None
        try:
            conn = self._store._connect()
            if action == "read":
                on_date = _parse_date(str(params.get("date", "") or ""))
                prefs = self._store.preferences(conn)
                entry = self._store.todays_entry(
                    conn,
                    on_date=on_date,
                    tradition=str(params.get("tradition") or prefs["tradition"]),
                    translation=str(params.get("translation") or prefs["translation"]),
                )
                if entry is None:
                    return ToolResult(
                        tool_name=self.tool_id,
                        content="No devotional content is configured.",
                        success=False,
                    )
                content = (
                    f"Devotional date: {on_date.isoformat()}\n"
                    f"Tradition: {entry['tradition']}\n"
                    f"Translation: {entry['translation']}\n"
                    f"Scripture reference: {entry['scripture_reference']}\n"
                    f"Scripture text: {entry['scripture_text']}\n"
                    f"Theme: {entry['theme']}\n"
                    f"Reflection prompt: {entry['reflection_prompt']}\n"
                    f"Prayer prompt: {entry['prayer_prompt']}\n"
                    f"Practice: {entry['practice']}\n"
                    f"Worship focus: {prefs['worship_focus']}\n"
                    f"Prayer topics: {', '.join(prefs['prayer_topics']) or 'none configured'}\n"
                    f"Preferred time: {prefs['preferred_time']}"
                )
                return ToolResult(
                    tool_name=self.tool_id,
                    content=content,
                    success=True,
                    metadata={
                        "date": on_date.isoformat(),
                        "entry_id": entry["id"],
                        "scripture_reference": entry["scripture_reference"],
                        "translation": entry["translation"],
                    },
                )

            if action == "add":
                required = ("scripture_reference", "scripture_text")
                missing = [name for name in required if not str(params.get(name, "")).strip()]
                if missing:
                    return ToolResult(
                        tool_name=self.tool_id,
                        content=f"Missing required fields: {', '.join(missing)}",
                        success=False,
                    )
                entry = self._store.add_entry(conn, params)
                return ToolResult(
                    tool_name=self.tool_id,
                    content=f"Added devotional entry '{entry.get('slug', '')}'.",
                    success=True,
                    metadata={"slug": entry.get("slug", "")},
                )

            if action == "set_preferences":
                prefs = self._store.update_preferences(conn, params)
                return ToolResult(
                    tool_name=self.tool_id,
                    content=(
                        "Updated devotional preferences: "
                        f"tradition={prefs['tradition']}, "
                        f"translation={prefs['translation']}, "
                        f"preferred_time={prefs['preferred_time']}"
                    ),
                    success=True,
                    metadata=prefs,
                )

            return ToolResult(
                tool_name=self.tool_id,
                content=f"Unsupported devotional action: {action}",
                success=False,
            )
        except (ValueError, KeyError, sqlite3.Error) as exc:
            return ToolResult(
                tool_name=self.tool_id,
                content=f"Devotional store error: {exc}",
                success=False,
            )
        finally:
            if conn is not None:
                conn.close()


__all__ = ["DevotionalContentTool", "DevotionalStore"]
