"""Remote board parsers, using canned API responses (no network)."""

from datetime import UTC, datetime, timedelta

import httpx

from applypilot.discovery import remote_boards as rb

NOW = datetime(2026, 11, 12, tzinfo=UTC)

PAYLOADS = {
    "remoteok.com/api": [
        {"legal": "..."},
        {"position": "Senior DevOps Engineer", "company": "Acme", "url": "https://remoteOK.com/j/1",
         "apply_url": "https://remoteOK.com/j/1", "epoch": int(NOW.timestamp()) - 3600,
         "tags": ["devops", "aws"], "description": "<p>" + "x" * 300 + "</p>",
         "salary_min": 120000, "salary_max": 150000, "location": ""},
        {"position": "Graphic Designer", "company": "Foo", "url": "https://remoteOK.com/j/2",
         "epoch": int(NOW.timestamp()) - 3600, "tags": ["design"]},
    ],
    "remotive.com/api/remote-jobs": {"jobs": [
        {"url": "https://remotive.com/1", "title": "DevOps Engineer", "company_name": "Globex",
         "publication_date": (NOW - timedelta(days=10)).isoformat(), "tags": [],
         "candidate_required_location": "Worldwide", "description": "old"},
    ]},
    "weworkremotely.com/categories/remote-devops-sysadmin-jobs.rss": b"""<?xml version="1.0"?>
<rss><channel><item><title>Knack: Cloud Engineer</title><region>Anywhere in the World</region>
<link>https://weworkremotely.com/remote-jobs/knack-cloud-engineer</link>
<pubDate>Wed, 11 Nov 2026 10:00:00 +0000</pubDate><category>DevOps and Sysadmin</category>
<description>&lt;p&gt;Run our cloud&lt;/p&gt;</description></item></channel></rss>""",
}


def _client():
    def handler(req: httpx.Request):
        key = f"{req.url.host.removeprefix('www.')}{req.url.path}"
        body = PAYLOADS.get(key)
        if body is None:
            return httpx.Response(404)
        if isinstance(body, bytes):
            return httpx.Response(200, content=body)
        return httpx.Response(200, json=body)
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_remoteok_parse_and_filter():
    jobs = rb.fetch_remoteok(_client(), ["devops engineer"], {})
    assert len(jobs) == 2
    kept = rb.filter_jobs(jobs, ["devops engineer"], 72, [], now=NOW)
    assert [j["title"] for j in kept] == ["Senior DevOps Engineer"]
    assert kept[0]["company"] == "Acme" and kept[0]["salary"] == "$120,000-$150,000/year"
    assert kept[0]["full_description"]


def test_old_jobs_dropped():
    jobs = rb.fetch_remotive(_client(), ["devops"], {})
    assert rb.filter_jobs(jobs, ["devops"], 72, [], now=NOW) == []


def test_wwr_splits_company_from_title():
    jobs = rb.fetch_weworkremotely(_client(), [], {"wwr_categories": ["remote-devops-sysadmin-jobs"]})
    assert jobs[0]["company"] == "Knack" and jobs[0]["title"] == "Cloud Engineer"
    assert jobs[0]["posted_at"].year == 2026


def test_exclude_titles():
    jobs = [{"url": "u", "title": "DevOps Intern", "tags": []}]
    assert rb.filter_jobs(jobs, ["devops"], None, ["intern"]) == []


def test_matches_query_needs_all_words():
    assert rb.matches_query({"title": "Site Reliability Engineer", "tags": []}, ["site reliability engineer"])
    assert not rb.matches_query({"title": "Site Manager", "tags": []}, ["site reliability engineer"])
