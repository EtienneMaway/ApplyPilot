"""Content ideas aimed at the companies you're applying to.

Reads what the scored jobs ask for (requirements + skill gaps) and which
companies you applied to, then asks the LLM for posts, videos and small
projects that show you doing exactly those things. Stored in the
`content_ideas` table and shown on the board page.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from datetime import UTC, datetime

from applypilot import config
from applypilot.database import get_connection
from applypilot.gaps import compute_gaps
from applypilot.style import HUMAN_STYLE, ai_tells

log = logging.getLogger(__name__)

_PROMPT = """You plan content for a software engineer who is job hunting and publishes a public learning
series ("DevOps in 90 Days": daily notes and short videos). The goal: hiring managers at the companies below
see the content and think "this person already does what we need".

Use the DATA. Every idea must point at requirements or gaps that show up in several jobs, and name which
companies it speaks to. Prefer ideas the engineer can make in a day or two with their real stack, and ideas
that close a gap in public (learning Kubernetes on camera counts as evidence).

{style}

Return JSON only:
{{"ideas": [
  {{"title": "short working title",
    "format": "LinkedIn post | short video | long video | GitHub repo | write-up",
    "hook": "the first line as the engineer would write it, plain and specific",
    "what_to_show": "2-3 sentences: what to build or demo and what the viewer sees",
    "skills": ["skills or requirements it targets"],
    "companies": ["companies from the data it speaks to"],
    "why": "one sentence: which repeated requirement or gap this answers, with the count"}}
]}}
Give 10 ideas, most useful first."""


def _ensure_table(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS content_ideas (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT,
            title      TEXT,
            format     TEXT,
            hook       TEXT,
            what_to_show TEXT,
            skills     TEXT,
            companies  TEXT,
            why        TEXT,
            status     TEXT DEFAULT 'idea'
        )
    """)
    conn.commit()


def _data_summary(conn, min_score: int) -> str:
    rows = conn.execute(
        "SELECT company, site, title, requirements, company_country, apply_status FROM jobs "
        "WHERE fit_score >= ? AND requirements IS NOT NULL", (min_score,)
    ).fetchall()
    req_counts: Counter = Counter()
    for r in rows:
        for item in (r["requirements"] or "").split(";"):
            if item.strip():
                req_counts[item.strip().lower()] += 1
    gaps = compute_gaps(min_score=min_score, conn=conn)[:20]
    companies = Counter((r["company"] or r["site"]) for r in rows)
    applied = sorted({(r["company"] or r["site"]) for r in rows if r["apply_status"] == "applied"})
    lines = [f"JOBS CONSIDERED: {len(rows)} (fit {min_score}+)", "", "MOST REQUESTED:"]
    lines += [f"- {k} ({n})" for k, n in req_counts.most_common(30)]
    lines += ["", "SKILL GAPS (asked for, not on the resume):"]
    lines += [f"- {g['skill']} ({g['jobs']} jobs)" for g in gaps]
    lines += ["", "COMPANIES (jobs):"] + [f"- {c} ({n})" for c, n in companies.most_common(25)]
    if applied:
        lines += ["", "ALREADY APPLIED TO: " + ", ".join(applied)]
    return "\n".join(lines)


def generate_ideas(min_score: int = 4) -> int:
    from applypilot.llm import get_client

    conn = get_connection()
    _ensure_table(conn)
    resume = config.RESUME_PATH.read_text(encoding="utf-8")
    user = f"DATA:\n{_data_summary(conn, min_score)}\n\nENGINEER'S RESUME:\n{resume}"
    client = get_client()
    ideas: list[dict] = []
    for _ in range(3):
        raw = client.chat([{"role": "system", "content": _PROMPT.format(style=HUMAN_STYLE)},
                           {"role": "user", "content": user}], max_tokens=4000)
        try:
            ideas = json.loads(raw[raw.find("{"): raw.rfind("}") + 1]).get("ideas", [])
        except (ValueError, json.JSONDecodeError):
            continue
        tells = ai_tells(" ".join(f"{i.get('hook', '')} {i.get('what_to_show', '')}" for i in ideas))
        if not tells:
            break
        user += f"\n\nLast draft had AI-sounding phrasing ({'; '.join(tells[:5])}). Rewrite plainly."
    if not ideas:
        raise RuntimeError("LLM returned no usable ideas")

    now = datetime.now(UTC).isoformat()
    conn.execute("DELETE FROM content_ideas WHERE status = 'idea'")  # keep ones you marked
    for i in ideas:
        conn.execute(
            "INSERT INTO content_ideas (created_at, title, format, hook, what_to_show, skills, companies, why) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (now, i.get("title"), i.get("format"), i.get("hook"), i.get("what_to_show"),
             ", ".join(i.get("skills") or []), ", ".join(i.get("companies") or []), i.get("why")),
        )
    conn.commit()
    return len(ideas)


def list_ideas() -> list[dict]:
    conn = get_connection()
    _ensure_table(conn)
    return [dict(r) for r in conn.execute("SELECT * FROM content_ideas ORDER BY id")]
