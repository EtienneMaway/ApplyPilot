"""Skill gaps: what the jobs you're seeing ask for that your resume doesn't show.

The scorer writes a MISSING list for every job. This module counts those
skills across jobs, keeps your own status for each one (todo / learning /
posted, plus a note such as a video link), and writes the result to
~/.applypilot/skill_gaps.md so it can feed posts and videos.
"""

from __future__ import annotations

import re
import sqlite3
from collections import Counter, defaultdict
from datetime import UTC, datetime

from applypilot.config import APP_DIR
from applypilot.database import get_connection

GAPS_MD = APP_DIR / "skill_gaps.md"

STATUSES = ("todo", "learning", "posted", "skip")

_ALIASES = {
    "k8s": "kubernetes", "golang": "go", "gcp": "google cloud", "google cloud platform": "google cloud",
    "amazon web services": "aws", "ms azure": "azure", "microsoft azure": "azure",
    "postgres": "postgresql", "js": "javascript", "ts": "typescript", "node": "node.js", "nodejs": "node.js",
    "ci cd": "ci/cd", "cicd": "ci/cd", "iac": "infrastructure as code", "tf": "terraform",
    "ml": "machine learning", "llms": "llm", "large language models": "llm",
}


def skill_key(name: str) -> str:
    k = re.sub(r"\s+", " ", re.sub(r"[^\w+#./ ]", " ", name.lower())).strip()
    k = re.sub(r"^(experience with|knowledge of|familiarity with|strong|proficiency in)\s+", "", k)
    if "degree" in k and not k.startswith(("master", "phd", "doctor")):
        return "completed degree"
    if k.startswith("on call") or k.startswith("on-call"):
        return "on-call experience"
    return _ALIASES.get(k, k)


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS skill_notes (
            skill      TEXT PRIMARY KEY,
            status     TEXT NOT NULL DEFAULT 'todo',
            note       TEXT,
            updated_at TEXT
        )
    """)
    conn.commit()


def set_status(skill: str, status: str, note: str | None = None) -> str:
    if status not in STATUSES:
        raise ValueError(f"status must be one of {', '.join(STATUSES)}")
    conn = get_connection()
    _ensure_table(conn)
    key = skill_key(skill)
    conn.execute(
        "INSERT INTO skill_notes (skill, status, note, updated_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(skill) DO UPDATE SET status = excluded.status, "
        "note = COALESCE(excluded.note, skill_notes.note), updated_at = excluded.updated_at",
        (key, status, note, datetime.now(UTC).isoformat()),
    )
    conn.commit()
    return key


def compute_gaps(min_score: int = 4, conn: sqlite3.Connection | None = None) -> list[dict]:
    """Aggregate missing skills across scored jobs with fit_score >= min_score.

    Low scores are left out by default because they're mostly jobs in a
    different field or ones you can't take from your location, which would
    drown the list in noise.
    """
    conn = conn or get_connection()
    _ensure_table(conn)
    rows = conn.execute(
        "SELECT title, company, site, url, fit_score, apply_status, missing_skills FROM jobs "
        "WHERE missing_skills IS NOT NULL AND missing_skills != '' AND fit_score >= ?",
        (min_score,),
    ).fetchall()
    total = conn.execute("SELECT COUNT(*) FROM jobs WHERE fit_score >= ?", (min_score,)).fetchone()[0]
    notes = {r["skill"]: dict(r) for r in conn.execute("SELECT * FROM skill_notes")}

    counts: Counter = Counter()
    labels: dict[str, Counter] = defaultdict(Counter)
    scores: dict[str, list[int]] = defaultdict(list)
    examples: dict[str, list[str]] = defaultdict(list)
    applied: Counter = Counter()

    for r in rows:
        seen = set()
        for raw in r["missing_skills"].split(","):
            raw = raw.strip().strip(".")
            if not raw or len(raw) > 60:
                continue
            key = skill_key(raw)
            if key in seen:
                continue
            seen.add(key)
            counts[key] += 1
            labels[key][raw] += 1
            scores[key].append(r["fit_score"])
            if r["apply_status"] == "applied":
                applied[key] += 1
            if len(examples[key]) < 3:
                examples[key].append(f"{r['title']} at {r['company'] or r['site']}")

    out = []
    for key, n in counts.most_common():
        note = notes.get(key, {})
        out.append({
            "skill": labels[key].most_common(1)[0][0],
            "key": key,
            "jobs": n,
            "share": n / total if total else 0.0,
            "avg_fit": sum(scores[key]) / len(scores[key]),
            "in_applied": applied[key],
            "status": note.get("status", "todo"),
            "note": note.get("note") or "",
            "examples": examples[key],
        })
    return out


def write_markdown(gaps: list[dict], min_score: int) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        "# Skill gaps",
        "",
        f"Updated {now}. Skills that scored jobs (fit {min_score}+) ask for and the resume doesn't show,",
        "most requested first. Update a row with `applypilot gaps --set SKILL --status learning|posted --note TEXT`.",
        "",
        "| # | Skill | Jobs | Share | Avg fit | Status | Note |",
        "|---|---|---|---|---|---|---|",
    ]
    for i, g in enumerate(gaps, 1):
        lines.append(
            f"| {i} | {g['skill']} | {g['jobs']} | {g['share']:.0%} | {g['avg_fit']:.1f} | {g['status']} | {g['note']} |"
        )
    lines += ["", "## Where each one came from", ""]
    for g in gaps[:30]:
        lines.append(f"- **{g['skill']}**: " + "; ".join(g["examples"]))
    text = "\n".join(lines) + "\n"
    GAPS_MD.parent.mkdir(parents=True, exist_ok=True)
    GAPS_MD.write_text(text, encoding="utf-8")
    return str(GAPS_MD)
