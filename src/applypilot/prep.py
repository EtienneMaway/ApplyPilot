"""Interview prep for jobs already applied to.

For each application: what the tailored resume reworded or pushed forward
compared with the original, and the questions the job is likely to bring up,
with answers drawn from the applicant's real experience. Shown on the board
next to the form answers the agent logged.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from applypilot import config
from applypilot.database import get_connection
from applypilot.style import HUMAN_STYLE

log = logging.getLogger(__name__)

_PROMPT = """You help an applicant prepare for a call about a job they applied to.

You get their ORIGINAL resume, the TAILORED resume that was sent, and the job.

1. "resume_changes": every place the tailored resume says something more strongly, more broadly or differently
   than the original (new emphasis, merged facts, reworded scope, a skill moved to the top). Quote the sent line,
   say what the original actually said, and give a short note on how to talk about it honestly on a call.
   Skip pure formatting. [] if nothing changed in substance.
2. "likely_questions": 5-6 questions this job will probably ask, given its requirements, especially where the
   resume is thin. For each, a talking point from the applicant's real experience, and if it's a gap,
   what to say and what to learn before the call.

{style}

Return JSON only:
{{"resume_changes": [{{"sent": "...", "original": "...", "how_to_talk_about_it": "..."}}],
  "likely_questions": [{{"q": "...", "talking_point": "...", "gap": false}}]}}"""


def build_prep(job: dict, original: str) -> dict:
    from applypilot.llm import get_client

    tailored = ""
    if job.get("tailored_resume_path") and Path(job["tailored_resume_path"]).exists():
        tailored = Path(job["tailored_resume_path"]).read_text(encoding="utf-8")
    user = (f"JOB: {job.get('title')} at {job.get('company') or job.get('site')}\n"
            f"REQUIREMENTS: {job.get('requirements') or ''}\n"
            f"DESCRIPTION:\n{(job.get('full_description') or '')[:5000]}\n\n"
            f"ORIGINAL RESUME:\n{original}\n\nTAILORED RESUME (sent):\n{tailored or '(same as original)'}")
    raw = get_client().chat([{"role": "system", "content": _PROMPT.format(style=HUMAN_STYLE)},
                             {"role": "user", "content": user}], max_tokens=3000)
    data = json.loads(raw[raw.find("{"): raw.rfind("}") + 1])
    # Models sometimes rename fields; map them back so the board can rely on them.
    for c in data.get("resume_changes", []):
        if "how_to_talk_about_it" not in c:
            c["how_to_talk_about_it"] = next((v for k, v in c.items() if k not in ("sent", "original")), "")
    for q in data.get("likely_questions", []):
        if "talking_point" not in q:
            q["talking_point"] = next((v for k, v in q.items() if k not in ("q", "gap") and isinstance(v, str)), "")
        q.setdefault("gap", False)
    return data


def build_all(rebuild: bool = False) -> int:
    conn = get_connection()
    original = config.RESUME_PATH.read_text(encoding="utf-8")
    rows = conn.execute("SELECT * FROM jobs WHERE applied_at IS NOT NULL" +
                        ("" if rebuild else " AND interview_prep IS NULL")).fetchall()
    made = 0
    for r in rows:
        try:
            prep = build_prep(dict(r), original)
        except Exception as e:  # keep going; prep is a convenience
            log.error("Prep failed for %s: %s", r["url"], e)
            continue
        conn.execute("UPDATE jobs SET interview_prep = ? WHERE url = ?", (json.dumps(prep), r["url"]))
        conn.commit()
        made += 1
    return made
