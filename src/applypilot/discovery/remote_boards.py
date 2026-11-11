"""Remote job boards via their public APIs and RSS feeds.

Upstream ApplyPilot reaches these boards through `smartextract`, which loads
each page in a browser and asks an LLM to find the job cards. These boards
publish structured feeds, so this module reads them directly: no browser, no
LLM tokens, and the company name comes back as a field (needed for dedup).

Boards: Remote OK, Remotive, Himalayas, Jobicy, Working Nomads, We Work Remotely.

Each board asks API users to credit it and link back to the original listing.
We store the board's own URL as the job URL, so every record points back to it.

Config (searches.yaml), all optional:

    remote_boards:
      enabled: true
      boards: [remoteok, remotive, himalayas, jobicy, workingnomads, weworkremotely]
      max_age_hours: 72          # falls back to defaults.hours_old
      allowed_regions:           # drop jobs restricted to regions not in this list
        - worldwide              # (jobs with no stated restriction are kept)
        - anywhere
        - africa
        - emea
      wwr_categories:            # We Work Remotely RSS categories
        - remote-devops-sysadmin-jobs
        - remote-back-end-programming-jobs
        - remote-full-stack-programming-jobs
"""

from __future__ import annotations

import html
import logging
import re
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta

import httpx

from applypilot import config
from applypilot.database import get_connection, init_db, store_jobs

log = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) job-hunter/0.1"
TIMEOUT = 25.0

DEFAULT_WWR_CATEGORIES = [
    "remote-devops-sysadmin-jobs",
    "remote-back-end-programming-jobs",
    "remote-full-stack-programming-jobs",
]


# -- helpers -----------------------------------------------------------------

def _strip_html(text: str | None) -> str:
    if not text:
        return ""
    text = re.sub(r"<(br|/p|/li|/h\d)[^>]*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _parse_date(value) -> datetime | None:
    if value in (None, "", 0):
        return None
    try:
        if isinstance(value, (int, float)) or str(value).isdigit():
            return datetime.fromtimestamp(int(value), tz=UTC)
        s = str(value).replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            from email.utils import parsedate_to_datetime
            dt = parsedate_to_datetime(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except Exception:
        return None


def _salary(lo, hi, period: str = "", currency: str = "") -> str | None:
    try:
        lo, hi = int(float(lo or 0)), int(float(hi or 0))
    except (TypeError, ValueError):
        return None
    if not lo and not hi:
        return None
    cur = "$" if currency in ("", "USD") else f"{currency} "
    s = f"{cur}{lo:,}-{cur}{hi:,}" if lo and hi else f"{cur}{(lo or hi):,}"
    return f"{s}/{period}" if period else s


def matches_query(job: dict, queries: list[str]) -> bool:
    """True if every word of at least one query appears in the title or tags."""
    if not queries:
        return True
    hay = f"{job.get('title', '')} {' '.join(job.get('tags') or [])}".lower()
    hay_words = set(re.findall(r"[a-z0-9+#.]+", hay))
    for q in queries:
        words = re.findall(r"[a-z0-9+#.]+", q.lower())
        if words and all(w in hay_words or w in hay for w in words):
            return True
    return False


def _excluded(title: str, exclude: list[str]) -> bool:
    t = (title or "").lower()
    return any(x.lower() in t for x in exclude)


def _job(**kw) -> dict:
    desc = kw.get("full_description") or ""
    kw["description"] = desc[:500] if desc else kw.get("description")
    kw["full_description"] = desc if len(desc) > 200 else None
    return kw


# -- board fetchers ----------------------------------------------------------
# Each returns a list of normalized dicts:
#   url, title, company, location, salary, full_description,
#   application_url, posted_at (datetime|None), tags (list[str])

def fetch_remoteok(client: httpx.Client, queries: list[str], cfg: dict) -> list[dict]:
    data = client.get("https://remoteok.com/api").json()
    out = []
    for j in data[1:]:  # first element is the legal notice
        if not isinstance(j, dict) or not j.get("position"):
            continue
        out.append(_job(
            url=j.get("url"), title=j.get("position"), company=j.get("company"),
            location=j.get("location") or "Remote",
            salary=_salary(j.get("salary_min"), j.get("salary_max"), "year"),
            full_description=_strip_html(j.get("description")),
            application_url=j.get("apply_url") or j.get("url"),
            posted_at=_parse_date(j.get("epoch") or j.get("date")),
            tags=j.get("tags") or [],
        ))
    return out


def fetch_remotive(client: httpx.Client, queries: list[str], cfg: dict) -> list[dict]:
    seen, out = set(), []
    for q in queries or [""]:
        data = client.get("https://remotive.com/api/remote-jobs", params={"search": q, "limit": 100}).json()
        for j in data.get("jobs", []):
            if j.get("url") in seen:
                continue
            seen.add(j.get("url"))
            out.append(_job(
                url=j.get("url"), title=j.get("title"), company=j.get("company_name"),
                location=f"Remote ({j.get('candidate_required_location') or 'Anywhere'})",
                salary=j.get("salary") or None,
                full_description=_strip_html(j.get("description")),
                application_url=j.get("url"),
                posted_at=_parse_date(j.get("publication_date")),
                tags=j.get("tags") or [],
            ))
    return out


def fetch_himalayas(client: httpx.Client, queries: list[str], cfg: dict) -> list[dict]:
    seen, out = set(), []
    for q in queries or [""]:
        url = "https://himalayas.app/jobs/api/search" if q else "https://himalayas.app/jobs/api"
        for page in (1, 2):
            params = {"q": q, "page": page} if q else {"limit": 50}
            data = client.get(url, params=params).json()
            jobs = data.get("jobs", [])
            for j in jobs:
                link = j.get("applicationLink") or j.get("guid")
                if link in seen:
                    continue
                seen.add(link)
                locs = j.get("locationRestrictions") or []
                out.append(_job(
                    url=j.get("guid") or link, title=j.get("title"), company=j.get("companyName"),
                    location="Remote" + (f" ({', '.join(locs)})" if locs else ""),
                    salary=_salary(j.get("minSalary"), j.get("maxSalary"),
                                   {"annual": "year"}.get(j.get("salaryPeriod"), j.get("salaryPeriod") or ""),
                                   j.get("currency") or ""),
                    full_description=_strip_html(j.get("description") or j.get("excerpt")),
                    application_url=link,
                    posted_at=_parse_date(j.get("pubDate")),
                    tags=(j.get("categories") or []) + (j.get("seniority") or []),
                ))
            if not q or len(jobs) < 20:
                break
    return out


def fetch_jobicy(client: httpx.Client, queries: list[str], cfg: dict) -> list[dict]:
    seen, out = set(), []
    # Jobicy's `tag` is a keyword search over title/description
    for q in queries or [""]:
        params = {"count": 50}
        if q:
            params["tag"] = q
        data = client.get("https://jobicy.com/api/v2/remote-jobs", params=params).json()
        for j in data.get("jobs", []):
            if j.get("url") in seen:
                continue
            seen.add(j.get("url"))
            out.append(_job(
                url=j.get("url"), title=html.unescape(j.get("jobTitle") or ""), company=j.get("companyName"),
                location=f"Remote ({j.get('jobGeo') or 'Anywhere'})",
                salary=_salary(j.get("salaryMin") or j.get("annualSalaryMin"),
                               j.get("salaryMax") or j.get("annualSalaryMax"),
                               "year", j.get("salaryCurrency") or ""),
                full_description=_strip_html(j.get("jobDescription")),
                application_url=j.get("url"),
                posted_at=_parse_date(j.get("pubDate")),
                tags=(j.get("jobIndustry") or []) + [str(j.get("jobLevel") or "")],
            ))
    return out


def fetch_workingnomads(client: httpx.Client, queries: list[str], cfg: dict) -> list[dict]:
    data = client.get("https://www.workingnomads.com/api/exposed_jobs/").json()
    out = []
    for j in data:
        out.append(_job(
            url=j.get("url"), title=j.get("title"), company=j.get("company_name"),
            location=f"Remote ({j.get('location') or 'Anywhere'})",
            salary=None,
            full_description=_strip_html(j.get("description")),
            application_url=j.get("url"),
            posted_at=_parse_date(j.get("pub_date")),
            tags=[t.strip() for t in (j.get("tags") or "").split(",") if t.strip()] + [j.get("category_name") or ""],
        ))
    return out


def fetch_weworkremotely(client: httpx.Client, queries: list[str], cfg: dict) -> list[dict]:
    out = []
    for cat in cfg.get("wwr_categories") or DEFAULT_WWR_CATEGORIES:
        r = client.get(f"https://weworkremotely.com/categories/{cat}.rss")
        if r.status_code != 200:
            log.warning("WWR category %s: HTTP %s", cat, r.status_code)
            continue
        root = ET.fromstring(r.content)
        for item in root.iter("item"):
            raw_title = (item.findtext("title") or "").strip()
            # WWR titles are "Company: Job Title"
            company, _, title = raw_title.partition(": ")
            if not title:
                company, title = "", raw_title
            link = (item.findtext("link") or "").strip()
            out.append(_job(
                url=link, title=title, company=company or None,
                location=f"Remote ({(item.findtext('region') or 'Anywhere').strip()})",
                salary=None,
                full_description=_strip_html(item.findtext("description")),
                application_url=link,
                posted_at=_parse_date(item.findtext("pubDate")),
                tags=[(item.findtext("category") or "").strip(), (item.findtext("skills") or "").strip()],
            ))
    return out


BOARDS: dict[str, tuple[str, Callable[[httpx.Client, list[str], dict], list[dict]]]] = {
    "remoteok": ("RemoteOK", fetch_remoteok),
    "remotive": ("Remotive", fetch_remotive),
    "himalayas": ("Himalayas", fetch_himalayas),
    "jobicy": ("Jobicy", fetch_jobicy),
    "workingnomads": ("Working Nomads", fetch_workingnomads),
    "weworkremotely": ("WeWorkRemotely", fetch_weworkremotely),
}


# -- filtering + entry point -------------------------------------------------

_OPEN_REGION = ("anywhere", "worldwide", "global", "world", "international")


def region_ok(location: str | None, allowed: list[str]) -> bool:
    """Keep a remote job unless it names a restriction outside `allowed`."""
    if not allowed or not location:
        return True
    m = re.search(r"\((.*)\)", location)
    restriction = (m.group(1) if m else location.replace("Remote", "")).strip().lower()
    if not restriction:
        return True
    return any(tok.lower() in restriction for tok in (*allowed, *_OPEN_REGION))


def filter_jobs(jobs: Iterable[dict], queries: list[str], max_age_hours: int | None,
                exclude_titles: list[str], now: datetime | None = None,
                allowed_regions: list[str] | None = None) -> list[dict]:
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(hours=max_age_hours) if max_age_hours else None
    kept = []
    for j in jobs:
        if not j.get("url") or not j.get("title"):
            continue
        if cutoff and j.get("posted_at") and j["posted_at"] < cutoff:
            continue
        if _excluded(j["title"], exclude_titles):
            continue
        if not matches_query(j, queries):
            continue
        if not region_ok(j.get("location"), allowed_regions or []):
            continue
        kept.append(j)
    return kept


def run_remote_boards(boards: list[str] | None = None) -> dict:
    """Fetch all enabled remote boards and store matching jobs. Returns per-board stats."""
    search_cfg = config.load_search_config() or {}
    rb_cfg = search_cfg.get("remote_boards") or {}
    if rb_cfg.get("enabled") is False:
        log.info("remote_boards disabled in searches.yaml")
        return {}

    queries = [q["query"] if isinstance(q, dict) else str(q) for q in search_cfg.get("queries", [])]
    max_age = rb_cfg.get("max_age_hours") or (search_cfg.get("defaults") or {}).get("hours_old", 72)
    exclude = search_cfg.get("exclude_titles", [])
    selected = boards or rb_cfg.get("boards") or list(BOARDS)

    init_db()
    conn = get_connection()
    stats: dict = {}

    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT, follow_redirects=True) as client:
        for key in selected:
            if key not in BOARDS:
                log.warning("Unknown remote board: %s (known: %s)", key, ", ".join(BOARDS))
                continue
            label, fetch = BOARDS[key]
            try:
                raw = fetch(client, queries, rb_cfg)
                kept = filter_jobs(raw, queries, max_age, exclude,
                                   allowed_regions=rb_cfg.get("allowed_regions"))
                new, existing = store_jobs(conn, kept, site=label, strategy="board_api")
                stats[key] = {"fetched": len(raw), "matched": len(kept), "new": new, "existing": existing}
                log.info("%s: fetched %d, matched %d, new %d", label, len(raw), len(kept), new)
            except Exception as e:  # one broken board must not stop the rest
                log.error("%s failed: %s", label, e)
                stats[key] = {"error": str(e)}
    return stats
