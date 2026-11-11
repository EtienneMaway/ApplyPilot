"""Cross-board dedup: the same posting must never be applied to twice."""

from datetime import UTC, datetime

import pytest

from applypilot import config, database
from applypilot.database import (
    find_applied_duplicate,
    init_db,
    make_apply_key,
    make_dedup_key,
    store_jobs,
)


@pytest.fixture
def conn(tmp_path, monkeypatch):
    db = tmp_path / "t.db"
    monkeypatch.setattr(database, "DB_PATH", db)
    monkeypatch.setattr(config, "DB_PATH", db)
    c = init_db(db)
    yield c
    database.close_connection(db)


@pytest.mark.parametrize("a,b", [
    (("Acme Inc.", "Senior DevOps Engineer"), ("ACME", "Sr. DevOps Engineer (Remote)")),
    (("Acme, LLC", "DevOps Engineer - Remote"), ("Acme", "DevOps Engineer")),
    (("Knack", "Cloud Engineer | Full-time"), ("knack", "cloud engineer")),
])
def test_same_posting_same_key(a, b):
    assert make_dedup_key(*a) == make_dedup_key(*b)


@pytest.mark.parametrize("a,b", [
    (("Acme", "DevOps Engineer"), ("Acme", "Senior DevOps Engineer")),
    (("Acme", "DevOps Engineer"), ("Globex", "DevOps Engineer")),
])
def test_different_postings_differ(a, b):
    assert make_dedup_key(*a) != make_dedup_key(*b)


def test_missing_company_gives_no_key():
    assert make_dedup_key(None, "DevOps Engineer") is None


def test_apply_key_strips_tracking():
    a = make_apply_key("https://boards.greenhouse.io/acme/jobs/123?gh_src=abc&utm_source=linkedin")
    b = make_apply_key("https://www.boards.greenhouse.io/acme/jobs/123/")
    assert a == b == "boards.greenhouse.io/acme/jobs/123"


def test_apply_key_keeps_indeed_job_id():
    a = make_apply_key("https://www.indeed.com/viewjob?jk=111&from=serp")
    b = make_apply_key("https://www.indeed.com/viewjob?jk=222")
    assert a != b


def _add(conn, url, company, title, site, apply_url=None):
    store_jobs(conn, [{"url": url, "title": title, "company": company,
                       "application_url": apply_url}], site=site, strategy="test")


def _apply(conn, url):
    conn.execute("UPDATE jobs SET apply_status='applied', applied_at=? WHERE url=?",
                 (datetime.now(UTC).isoformat(), url))
    conn.commit()


def test_same_job_on_two_boards_is_caught(conn):
    _add(conn, "https://indeed.com/viewjob?jk=1", "Acme Inc", "DevOps Engineer", "indeed")
    _add(conn, "https://linkedin.com/jobs/view/9", "ACME", "DevOps Engineer (Remote)", "linkedin")
    _apply(conn, "https://indeed.com/viewjob?jk=1")
    dup = find_applied_duplicate(conn, "https://linkedin.com/jobs/view/9")
    assert dup and dup["url"] == "https://indeed.com/viewjob?jk=1"


def test_same_apply_page_caught_without_company(conn):
    _add(conn, "https://remoteok.com/x", None, "Platform Eng", "RemoteOK",
         apply_url="https://jobs.lever.co/acme/abc?lever-source=remoteok")
    _add(conn, "https://himalayas.app/y", None, "Platform Engineer", "Himalayas",
         apply_url="https://jobs.lever.co/acme/abc")
    _apply(conn, "https://remoteok.com/x")
    assert find_applied_duplicate(conn, "https://himalayas.app/y")


def test_unrelated_job_not_flagged(conn):
    _add(conn, "https://a", "Acme", "DevOps Engineer", "indeed")
    _add(conn, "https://b", "Globex", "DevOps Engineer", "indeed")
    _apply(conn, "https://a")
    assert find_applied_duplicate(conn, "https://b") is None


def test_rediscovery_next_day_is_ignored(conn):
    """Running discovery again tomorrow must not create a second row for the same URL."""
    _add(conn, "https://a", "Acme", "DevOps Engineer", "indeed")
    _apply(conn, "https://a")
    new, existing = store_jobs(conn, [{"url": "https://a", "title": "DevOps Engineer", "company": "Acme"}],
                               site="indeed", strategy="test")
    assert (new, existing) == (0, 1)
    row = conn.execute("SELECT apply_status FROM jobs WHERE url='https://a'").fetchone()
    assert row["apply_status"] == "applied"


def test_acquire_job_skips_duplicates(conn, monkeypatch):
    from applypilot.apply import launcher
    monkeypatch.setattr(launcher, "_load_blocked", lambda: ([], []))
    for url, company, title in [
        ("https://indeed.com/viewjob?jk=1", "Acme", "DevOps Engineer"),
        ("https://linkedin.com/jobs/view/2", "Acme Inc.", "DevOps Engineer"),
        ("https://remotive.com/3", "Globex", "SRE"),
    ]:
        _add(conn, url, company, title, "x")
    conn.execute("UPDATE jobs SET tailored_resume_path='r.txt', fit_score=8")
    conn.execute("UPDATE jobs SET fit_score=7 WHERE url='https://remotive.com/3'")  # duplicate is checked first
    conn.commit()
    _apply(conn, "https://indeed.com/viewjob?jk=1")

    job = launcher.acquire_job(min_score=7)
    assert job["url"] == "https://remotive.com/3"
    status = conn.execute("SELECT apply_status FROM jobs WHERE url='https://linkedin.com/jobs/view/2'").fetchone()[0]
    assert status == "duplicate"


def test_backfill_existing_db(conn):
    """Rows created by upstream ApplyPilot (no keys) get keys on next start."""
    conn.execute("INSERT INTO jobs (url, title, site, strategy) VALUES ('https://w', 'Data Engineer', 'Acme', 'workday_api')")
    conn.commit()
    database.backfill_dedup_keys(conn)
    row = conn.execute("SELECT company, dedup_key FROM jobs WHERE url='https://w'").fetchone()
    assert row["company"] == "Acme" and row["dedup_key"] == "acme|data engineer"


def test_dry_run_not_recorded_as_applied(conn):
    from applypilot.apply.launcher import mark_result
    _add(conn, "https://a", "Acme", "DevOps Engineer", "x")
    mark_result("https://a", "dry_run", "dry run")
    row = conn.execute("SELECT apply_status, applied_at FROM jobs WHERE url='https://a'").fetchone()
    assert row["apply_status"] == "dry_run" and row["applied_at"] is None


def test_scorer_parses_missing():
    from applypilot.scoring.scorer import _parse_score_response
    r = _parse_score_response("SCORE: 7\nKEYWORDS: NestJS\nMISSING: Kubernetes, Terraform\nREASONING: ok")
    assert r["missing"] == "Kubernetes, Terraform" and r["score"] == 7
    assert _parse_score_response("SCORE: 9\nMISSING: none\nREASONING: x")["missing"] == ""


def test_gaps_aggregate(conn):
    from applypilot import gaps
    rows = [("https://1", "k8s, Terraform", 7), ("https://2", "Kubernetes, Go", 8),
            ("https://3", "Kubernetes", 2), ("https://4", "Golang", 6)]
    for url, miss, score in rows:
        _add(conn, url, "Acme", f"Eng {url}", "x")
        conn.execute("UPDATE jobs SET missing_skills=?, fit_score=? WHERE url=?", (miss, score, url))
    conn.commit()
    g = {r["key"]: r for r in gaps.compute_gaps(min_score=4, conn=conn)}
    assert g["kubernetes"]["jobs"] == 2       # k8s + Kubernetes; the score-2 job is excluded
    assert g["go"]["jobs"] == 2               # Go + Golang
    gaps.set_status("K8s", "posted", "https://youtu.be/x")
    g = {r["key"]: r for r in gaps.compute_gaps(min_score=4, conn=conn)}
    assert g["kubernetes"]["status"] == "posted"
