"""Writing checks, sign-up list, manual kits and the board page."""

import json

import pytest

from applypilot import config, database
from applypilot.database import init_db, store_jobs
from applypilot.style import ai_tells


@pytest.fixture
def conn(tmp_path, monkeypatch):
    db = tmp_path / "t.db"
    monkeypatch.setattr(database, "DB_PATH", db)
    monkeypatch.setattr(config, "DB_PATH", db)
    monkeypatch.setattr(config, "CHROME_WORKER_DIR", tmp_path / "chrome-workers")
    c = init_db(db)
    yield c
    database.close_connection(db)


@pytest.mark.parametrize("text", [
    "It isn't a side project, it's a platform.",
    "This role is not just about code but about people.",
    "I am thrilled to apply for this role.",
    "I leveraged NestJS to build a robust API.",
    "This demonstrates my ability to lead.",
    "Happy to walk through any of this in more detail.",
    "Built it fast — shipped in a week.",
])
def test_ai_tells_caught(text):
    assert ai_tells(text)


@pytest.mark.parametrize("text", [
    "I rebuilt ShareCARD's v2 core alone in 4.5 months and moved the team onto it.",
    "Built the subscriptions layer of a TypeScript payments SDK on ethers.js and USDC.",
])
def test_plain_text_passes(text):
    assert ai_tells(text) == []


def _job(conn, url, error, apply_url=None, fit=8):
    store_jobs(conn, [{"url": url, "title": "Backend Engineer", "company": "Acme", "application_url": apply_url}],
               site="x", strategy="t")
    conn.execute("UPDATE jobs SET apply_status='failed', apply_error=?, fit_score=?, apply_attempts=99, "
                 "tailored_resume_path='r.txt' WHERE url=?", (error, fit, url))
    conn.commit()


def test_signup_list_and_retry(conn, tmp_path):
    from applypilot import manual
    _job(conn, "https://himalayas.app/a", "login_issue")
    _job(conn, "https://himalayas.app/b", "login_issue", fit=6)
    _job(conn, "https://jobs.lever.co/x", "captcha")
    sites = manual.signup_sites()
    assert [s["domain"] for s in sites] == ["himalayas.app"]
    assert len(sites[0]["jobs"]) == 2 and sites[0]["signup_url"].endswith("/signup/talent")

    (tmp_path / "chrome-workers").mkdir()
    assert manual.retry_signup_jobs() == 2
    assert not (tmp_path / "chrome-workers").exists()
    status = conn.execute("SELECT apply_status FROM jobs WHERE url='https://himalayas.app/a'").fetchone()[0]
    assert status is None


def test_manual_jobs_skip_dead_and_login(conn):
    from applypilot import manual
    _job(conn, "https://a", "linkedin_easy_apply")
    _job(conn, "https://b", "not_eligible_location")
    _job(conn, "https://c", "login_issue")
    assert [j["url"] for j in manual.manual_jobs()] == ["https://a"]
    assert {j["url"] for j in manual.manual_jobs(include_login=True)} == {"https://a", "https://c"}


def test_board_renders(conn, tmp_path, monkeypatch):
    from applypilot import board
    monkeypatch.setattr(board, "BOARD_PATH", tmp_path / "board.html")
    _job(conn, "https://a", "linkedin_easy_apply")
    conn.execute("UPDATE jobs SET manual_kit=? WHERE url='https://a'",
                 (json.dumps({"answers": [{"q": "Email", "a": "x@y.z"}], "cover_letter": "Dear </script> test",
                              "resume_pdf": "", "you_must_answer": []}),))
    conn.commit()
    html = open(board.generate_board()).read()
    assert "__DATA__" not in html and "Backend Engineer" in html
    assert "Dear <\\/script>" in html  # embedded data can't close the script tag
