"""Jobs the bot can't finish: sign-up list and copy-paste application kits.

Two cases:
  1. The site needs an account (Himalayas, Workday portals...). These go on a
     sign-up list. Once the applicant has signed up in their normal Chrome,
     `retry_signup_jobs()` refreshes the bot's browser profile and puts the
     jobs back in the queue.
  2. Anything else that needs a person: LinkedIn Easy Apply, forms with
     personal questions, CAPTCHAs. Each gets a kit with the tailored resume,
     the cover letter and ready answers, so applying by hand is copy-paste.
"""

from __future__ import annotations

import json
import logging
import shutil
from collections import defaultdict
from datetime import datetime
from urllib.parse import urlsplit

from applypilot import config
from applypilot.config import APP_DIR, load_profile
from applypilot.database import get_connection
from applypilot.style import HUMAN_STYLE, ai_tells

log = logging.getLogger(__name__)

SIGNUP_MD = APP_DIR / "sign_up.md"

LOGIN_REASONS = ("login_issue", "account_required", "sso_required")
# Not worth a person's time: the job is closed or the applicant can't take it.
DEAD_REASONS = ("not_eligible_location", "not_eligible_salary", "expired", "already_applied",
                "not_a_job_application")

KNOWN_SIGNUP = {
    "himalayas.app": "https://himalayas.app/signup/talent",
    "wellfound.com": "https://wellfound.com/join",
    "linkedin.com": "https://www.linkedin.com/signup",
    "remotive.com": "https://remotive.com/accounts/signup",
    "weworkremotely.com": "https://weworkremotely.com/job-seekers/account/register",
    "workingnomads.com": "https://www.workingnomads.com/register",
    "jobicy.com": "https://jobicy.com/register",
    "arc.dev": "https://arc.dev/signup",
    "builtin.com": "https://builtin.com/auth/signup",
    "dynamitejobs.com": "https://dynamitejobs.com/signup",
}


def _domain(url: str | None) -> str:
    if not url:
        return ""
    host = urlsplit(url).netloc.lower().removeprefix("www.")
    # myworkdayjobs: each employer has its own tenant and its own account
    return host


def _signup_url(domain: str) -> str:
    for known, link in KNOWN_SIGNUP.items():
        if domain.endswith(known):
            return link
    return f"https://{domain}"


# ---------------------------------------------------------------------------
# Sign-up list
# ---------------------------------------------------------------------------

def signup_sites() -> list[dict]:
    """Sites that blocked the bot with a login wall, with the jobs waiting there."""
    conn = get_connection()
    rows = conn.execute(
        f"SELECT url, application_url, title, company, site, fit_score FROM jobs "
        f"WHERE applied_at IS NULL AND apply_error IN ({','.join('?' * len(LOGIN_REASONS))}) "
        f"ORDER BY fit_score DESC",
        LOGIN_REASONS,
    ).fetchall()
    sites: dict[str, dict] = {}
    for r in rows:
        dom = _domain(r["application_url"] or r["url"])
        s = sites.setdefault(dom, {"domain": dom, "signup_url": _signup_url(dom), "jobs": [], "best_fit": 0})
        s["jobs"].append({"title": r["title"], "company": r["company"] or r["site"], "fit": r["fit_score"],
                          "url": r["application_url"] or r["url"]})
        s["best_fit"] = max(s["best_fit"], r["fit_score"] or 0)
    return sorted(sites.values(), key=lambda s: (-len(s["jobs"]), -s["best_fit"]))


def write_signup_md(sites: list[dict] | None = None) -> str:
    sites = signup_sites() if sites is None else sites
    lines = [
        "# Sites to sign up for",
        "",
        f"Updated {datetime.now():%Y-%m-%d %H:%M}. The bot reached these jobs but the site wanted an account.",
        "Sign up in your normal Chrome (stay logged in), tick the box, then run:",
        "",
        "    applypilot signups --retry",
        "",
        "That copies your logged-in Chrome profile to the bot and puts these jobs back in the queue.",
        "",
    ]
    if not sites:
        lines.append("Nothing waiting right now.")
    for s in sites:
        lines.append(f"- [ ] **{s['domain']}**: {len(s['jobs'])} job(s) waiting, best fit {s['best_fit']}/10. "
                     f"Sign up: {s['signup_url']}")
        for j in s["jobs"]:
            lines.append(f"    - {j['fit']}/10 {j['title']} at {j['company']}")
    SIGNUP_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(SIGNUP_MD)


def retry_signup_jobs() -> int:
    """Re-queue login-blocked jobs and make the bot re-copy the Chrome profile."""
    conn = get_connection()
    cur = conn.execute(
        f"UPDATE jobs SET apply_status = NULL, apply_error = NULL, apply_attempts = 0, apply_notes = NULL "
        f"WHERE applied_at IS NULL AND apply_error IN ({','.join('?' * len(LOGIN_REASONS))})",
        LOGIN_REASONS,
    )
    conn.commit()
    # Worker profiles are copied from Chrome once; remove them so the next run
    # picks up the sessions from the new sign-ups.
    if config.CHROME_WORKER_DIR.exists():
        shutil.rmtree(config.CHROME_WORKER_DIR, ignore_errors=True)
    return cur.rowcount


# ---------------------------------------------------------------------------
# Manual kits
# ---------------------------------------------------------------------------

def manual_jobs(include_login: bool = False) -> list[dict]:
    conn = get_connection()
    skip = list(DEAD_REASONS) + ([] if include_login else list(LOGIN_REASONS))
    rows = conn.execute(
        f"SELECT * FROM jobs WHERE applied_at IS NULL AND apply_status IN ('failed', 'manual') "
        f"AND COALESCE(apply_error, '') NOT IN ({','.join('?' * len(skip))}) "
        f"AND tailored_resume_path IS NOT NULL ORDER BY fit_score DESC",
        skip,
    ).fetchall()
    return [dict(r) for r in rows]


def _standard_answers(profile: dict) -> list[dict]:
    p, auth = profile.get("personal", {}), profile.get("work_authorization", {})
    comp, av = profile.get("compensation", {}), profile.get("availability", {})
    exp = profile.get("experience", {})
    cur = comp.get("salary_currency", "USD")
    pairs = [
        ("Full name", p.get("full_name")),
        ("Email", p.get("email")),
        ("Phone", p.get("phone")),
        ("Location", ", ".join(x for x in (p.get("city"), p.get("country")) if x)),
        ("LinkedIn", p.get("linkedin_url")),
        ("GitHub", p.get("github_url")),
        ("Portfolio / website", p.get("portfolio_url") or p.get("website_url")),
        ("Years of experience", exp.get("years_of_experience_total")),
        ("Current title", exp.get("current_job_title")),
        ("Authorized to work in the country of the job?", auth.get("legally_authorized_to_work")),
        ("Need visa sponsorship?", auth.get("require_sponsorship")),
        ("Open to relocation?", av.get("open_to_relocation")),
        ("Earliest start date", av.get("earliest_start_date")),
        ("Salary expectation", f"{comp.get('salary_expectation')} {cur} per year "
                               f"(range {comp.get('salary_range_min')}-{comp.get('salary_range_max')} {cur})"),
        ("Contract work OK?", av.get("available_for_contract")),
        ("Education", exp.get("education_level")),
    ]
    return [{"q": q, "a": str(a)} for q, a in pairs if a]


_KIT_PROMPT = """You prepare answers an applicant will paste into a job application form by hand.

Write in the applicant's voice, first person, from the RESUME only. Do not invent facts, numbers, tools or employers.

{style}

Return JSON only, no other text:
{{
  "why_this_job": "2-3 sentences: one concrete thing from this posting and the closest thing the applicant has built",
  "about_you": "3-4 sentences: who they are as an engineer, for 'tell us about yourself' boxes",
  "relevant_experience": "3-4 sentences on the experience closest to this job's requirements, with real numbers from the resume",
  "you_must_answer": ["questions from the BOT NOTES that only the applicant can answer, copied short; [] if none"]
}}"""


def build_kit(job: dict, profile: dict, resume_text: str) -> dict:
    from applypilot.llm import get_client

    cover = ""
    if job.get("cover_letter_path"):
        from pathlib import Path
        cl = Path(job["cover_letter_path"])
        txt = cl.with_suffix(".txt")
        cover = (txt if txt.exists() else cl).read_text(encoding="utf-8") if (txt.exists() or cl.exists()) else ""

    resume_pdf = ""
    if job.get("tailored_resume_path"):
        from pathlib import Path
        pdf = Path(job["tailored_resume_path"]).with_suffix(".pdf")
        resume_pdf = str(pdf) if pdf.exists() else job["tailored_resume_path"]

    user = (
        f"JOB: {job.get('title')} at {job.get('company') or job.get('site')}\n"
        f"REQUIREMENTS: {job.get('requirements') or ''}\n"
        f"DESCRIPTION:\n{(job.get('full_description') or '')[:5000]}\n\n"
        f"BOT NOTES (why the bot stopped):\n{job.get('apply_notes') or 'none'}\n\n"
        f"RESUME:\n{resume_text}"
    )
    generated: dict = {}
    client = get_client()
    for _ in range(3):
        raw = client.chat([{"role": "system", "content": _KIT_PROMPT.format(style=HUMAN_STYLE)},
                           {"role": "user", "content": user}], max_tokens=1500)
        try:
            generated = json.loads(raw[raw.find("{"): raw.rfind("}") + 1])
        except (ValueError, json.JSONDecodeError):
            continue
        prose = " ".join(str(generated.get(k, "")) for k in ("why_this_job", "about_you", "relevant_experience"))
        tells = ai_tells(prose)
        if not tells:
            break
        user += f"\n\nYour last draft had AI-sounding phrasing ({'; '.join(tells)}). Rewrite it plainly."

    answers = _standard_answers(profile)
    for key, q in (("why_this_job", "Why this job / company?"), ("about_you", "Tell us about yourself"),
                   ("relevant_experience", "Relevant experience")):
        if generated.get(key):
            answers.append({"q": q, "a": generated[key]})

    return {
        "apply_url": job.get("application_url") or job.get("url"),
        "resume_pdf": resume_pdf,
        "cover_letter": cover.strip(),
        "answers": answers,
        "you_must_answer": generated.get("you_must_answer") or [],
        "built_at": datetime.now().isoformat(timespec="seconds"),
    }


def build_kits(rebuild: bool = False) -> int:
    """Create kits for every job that needs a human, skipping ones already built."""
    conn = get_connection()
    profile = load_profile()
    resume_text = config.RESUME_PATH.read_text(encoding="utf-8")
    jobs = manual_jobs(include_login=True)
    made = 0
    for job in jobs:
        if job.get("manual_kit") and not rebuild:
            continue
        try:
            kit = build_kit(job, profile, resume_text)
        except Exception as e:  # one bad job shouldn't stop the rest
            log.error("Kit failed for %s: %s", job["url"], e)
            continue
        conn.execute("UPDATE jobs SET manual_kit = ? WHERE url = ?", (json.dumps(kit), job["url"]))
        conn.commit()
        made += 1
        log.info("Kit ready: %s at %s", job["title"], job.get("company") or job["site"])
    return made


def group_by_reason(jobs: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = defaultdict(list)
    for j in jobs:
        out[j.get("apply_error") or "manual"].append(j)
    return out
