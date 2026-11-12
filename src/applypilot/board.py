"""Local job-hunt board: one HTML page built from the database.

Tabs: Applied, Needs you (copy-paste kits), Sign up, What they ask for,
Content to make. Written to ~/.applypilot/board.html; nothing leaves the
machine. Regenerate with `applypilot board`.
"""

from __future__ import annotations

import json
import webbrowser
from datetime import datetime

from applypilot.config import APP_DIR
from applypilot.database import get_connection

BOARD_PATH = APP_DIR / "board.html"


def _collect() -> dict:
    from applypilot import content, gaps, manual

    conn = get_connection()
    cols = ("url, application_url, title, company, site, company_country, location, fit_score, requirements, "
            "missing_skills, apply_status, apply_error, applied_at, last_attempted_at, apply_notes, manual_kit, "
            "salary, discovered_at, submitted_answers")
    applied = [dict(r) for r in conn.execute(
        f"SELECT {cols} FROM jobs WHERE applied_at IS NOT NULL OR apply_status = 'applied' ORDER BY applied_at DESC")]
    scored = [dict(r) for r in conn.execute(
        f"SELECT {cols} FROM jobs WHERE fit_score IS NOT NULL ORDER BY fit_score DESC, discovered_at DESC")]
    needs = []
    for j in manual.manual_jobs(include_login=False):
        kit = json.loads(j["manual_kit"]) if j.get("manual_kit") else None
        needs.append({k: j.get(k) for k in ("url", "application_url", "title", "company", "site", "fit_score",
                                            "company_country", "apply_error", "apply_notes", "requirements")}
                     | {"kit": kit})
    stats = dict(conn.execute("""
        SELECT COUNT(*) AS found,
               SUM(fit_score IS NOT NULL) AS scored,
               SUM(fit_score >= 7) AS good_fit,
               SUM(applied_at IS NOT NULL) AS applied,
               SUM(apply_status = 'duplicate') AS duplicates_skipped
        FROM jobs""").fetchone())
    return {
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "stats": stats,
        "applied": applied,
        "scored": scored,
        "needs": needs,
        "signups": manual.signup_sites(),
        "gaps": gaps.compute_gaps(min_score=4)[:40],
        "ideas": content.list_ideas(),
    }


def generate_board(open_browser: bool = False) -> str:
    data = json.dumps(_collect(), default=str).replace("</", "<\\/")
    BOARD_PATH.parent.mkdir(parents=True, exist_ok=True)
    BOARD_PATH.write_text(_HTML.replace("__DATA__", data), encoding="utf-8")
    if open_browser:
        webbrowser.open(BOARD_PATH.as_uri())
    return str(BOARD_PATH)


_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Job Hunt Board</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,600&family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
:root{
  --bg:#f6f1ea; --panel:#fffdf9; --ink:#2a2622; --muted:#7a7068; --line:#e6ddd2;
  --accent:#c4552d; --accent-soft:#f3dccf; --good:#3f7d4e; --warn:#b07a1f; --bad:#a8432f;
}
@media (prefers-color-scheme: dark){
  :root:not([data-theme="light"]){
    --bg:#1e1b18; --panel:#262320; --ink:#efe8df; --muted:#a39a90; --line:#3a352f;
    --accent:#e07a50; --accent-soft:#3d2a20; --good:#7fbf8c; --warn:#e0b05a; --bad:#e08a75;
  }
}
:root[data-theme="dark"]{
  --bg:#1e1b18; --panel:#262320; --ink:#efe8df; --muted:#a39a90; --line:#3a352f;
  --accent:#e07a50; --accent-soft:#3d2a20; --good:#7fbf8c; --warn:#e0b05a; --bad:#e08a75;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 Inter,system-ui,sans-serif}
header{padding:28px 16px 8px;max-width:1200px;margin:0 auto}
h1{font-family:Fraunces,Georgia,serif;font-size:30px;margin:0 0 4px}
.sub{color:var(--muted);font-size:13px}
.stats{display:flex;flex-wrap:wrap;gap:10px;margin:18px 0 8px}
.stat{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:10px 14px;min-width:120px}
.stat b{display:block;font-size:22px;font-family:Fraunces,Georgia,serif}
.stat span{color:var(--muted);font-size:12px}
nav{max-width:1200px;margin:0 auto;padding:0 16px;display:flex;gap:4px;flex-wrap:wrap;border-bottom:1px solid var(--line)}
nav button{background:none;border:0;border-bottom:2px solid transparent;color:var(--muted);padding:10px 12px;font:500 14px Inter,sans-serif;cursor:pointer}
nav button.on{color:var(--ink);border-color:var(--accent)}
nav .n{font-family:'JetBrains Mono',monospace;font-size:11px;background:var(--accent-soft);color:var(--accent);border-radius:8px;padding:1px 6px;margin-left:4px}
main{max-width:1200px;margin:0 auto;padding:18px 16px 60px}
section{display:none} section.on{display:block}
.note{color:var(--muted);font-size:13px;margin:0 0 14px}
.tablewrap{overflow-x:auto;background:var(--panel);border:1px solid var(--line);border-radius:10px}
table{border-collapse:collapse;width:100%;min-width:820px}
th,td{text-align:left;padding:10px 12px;border-bottom:1px solid var(--line);vertical-align:top;font-size:13.5px}
th{font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);font-weight:600;background:var(--panel);position:sticky;top:0}
tr:last-child td{border-bottom:0}
td a{color:var(--accent);text-decoration:none} td a:hover{text-decoration:underline}
.fit{font-family:'JetBrains Mono',monospace;font-weight:500}
.f-hi{color:var(--good)} .f-mid{color:var(--warn)} .f-lo{color:var(--muted)}
.tags{display:flex;flex-wrap:wrap;gap:4px}
.tag{font-size:11.5px;padding:2px 7px;border-radius:6px;background:var(--bg);border:1px solid var(--line)}
.stretch{border-left:3px solid var(--accent);background:var(--accent-soft);border-radius:6px;padding:6px 9px;margin:6px 0}
.ans{padding:4px 0;border-bottom:1px solid var(--line);font-size:13px}
.ans .q{color:var(--muted);font-size:12px}
.tag.miss{border-color:var(--accent);color:var(--accent);background:var(--accent-soft)}
.pill{font-size:11px;padding:2px 8px;border-radius:9px;background:var(--bg);border:1px solid var(--line);white-space:nowrap}
.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:16px;margin-bottom:14px}
.card h3{margin:0 0 2px;font-size:16px}
.card .meta{color:var(--muted);font-size:12.5px;margin-bottom:10px}
details{margin-top:8px} summary{cursor:pointer;color:var(--muted);font-size:13px}
.why{white-space:pre-wrap;font-size:13px;background:var(--bg);border-radius:8px;padding:10px;margin-top:6px}
.qa{display:grid;grid-template-columns:minmax(150px,220px) 1fr auto;gap:6px 10px;align-items:start;margin-top:10px}
.qa .q{color:var(--muted);font-size:12.5px;padding-top:6px}
.qa .a{white-space:pre-wrap;font-size:13.5px;background:var(--bg);border-radius:8px;padding:6px 9px}
.copy{border:1px solid var(--line);background:var(--panel);color:var(--ink);border-radius:7px;padding:5px 9px;font-size:12px;cursor:pointer}
.copy.done{border-color:var(--good);color:var(--good)}
.big{white-space:pre-wrap;font-size:13.5px;background:var(--bg);border-radius:8px;padding:12px;max-height:340px;overflow:auto}
.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:10px}
.mono{font-family:'JetBrains Mono',monospace;font-size:12px}
.must{border-left:3px solid var(--accent);padding:6px 10px;margin-top:10px;font-size:13px}
.bar{height:8px;background:var(--accent);border-radius:4px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:14px}
.empty{color:var(--muted);padding:30px;text-align:center;background:var(--panel);border:1px dashed var(--line);border-radius:10px}
.filter{display:flex;gap:8px;margin-bottom:12px;flex-wrap:wrap}
.filter input,.filter select{background:var(--panel);color:var(--ink);border:1px solid var(--line);border-radius:8px;padding:7px 10px;font:14px Inter,sans-serif}
@media (max-width:640px){.qa{grid-template-columns:1fr auto}.qa .q{grid-column:1/-1;padding:0}}
</style>
</head>
<body>
<header>
  <h1>Job hunt</h1>
  <div class="sub" id="gen"></div>
  <div class="stats" id="stats"></div>
</header>
<nav id="tabs"></nav>
<main>
  <section id="applied">
    <p class="note">Every job the bot submitted. Use this when a company gets back to you.</p>
    <div class="filter"><input id="q" placeholder="Search company, role, country"><select id="which"><option value="applied">Applied</option><option value="scored">All scored jobs</option></select></div>
    <div id="appliedTable"></div>
  </section>
  <section id="needs">
    <p class="note">Good-fit jobs the bot couldn't finish (LinkedIn Easy Apply, personal questions, CAPTCHAs). Everything you need to apply by hand is here. After you apply, run <span class="mono">applypilot apply --mark-applied URL</span> so it's never repeated.</p>
    <div id="needsList"></div>
  </section>
  <section id="signup">
    <p class="note">These sites want an account before you can apply. Sign up in your normal Chrome and stay logged in, then run <span class="mono">applypilot signups --retry</span>. The bot picks up your sessions and tries these jobs again.</p>
    <div id="signupList"></div>
  </section>
  <section id="asks">
    <p class="note">What the jobs keep asking for that your resume doesn't show, counted across jobs that scored 4 or more.</p>
    <div id="gapTable"></div>
  </section>
  <section id="content">
    <p class="note">Posts, videos and repos aimed at the companies above, built from what they ask for. Refresh with <span class="mono">applypilot content</span>.</p>
    <div id="ideas" class="grid"></div>
  </section>
</main>
<script>
const D = __DATA__;
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fitCls = f => f >= 7 ? 'f-hi' : f >= 5 ? 'f-mid' : 'f-lo';
const split = (s, sep) => (s || '').split(sep).map(x => x.trim()).filter(Boolean);
const COPY = [];
function copyBtn(text){ COPY.push(text); return `<button class="copy" data-i="${COPY.length-1}">Copy</button>`; }
document.addEventListener('click', async e => {
  const b = e.target.closest('.copy'); if (!b) return;
  const t = COPY[+b.dataset.i];
  try { await navigator.clipboard.writeText(t); }
  catch { const ta = document.createElement('textarea'); ta.value = t; document.body.appendChild(ta); ta.select(); document.execCommand('copy'); ta.remove(); }
  b.textContent = 'Copied'; b.classList.add('done'); setTimeout(() => { b.textContent = 'Copy'; b.classList.remove('done'); }, 1400);
});

document.getElementById('gen').textContent = `Updated ${D.generated}. Rebuild with: applypilot board`;
const S = D.stats;
document.getElementById('stats').innerHTML = [
  ['Found', S.found], ['Scored', S.scored], ['Good fit (7+)', S.good_fit], ['Applied', S.applied],
  ['Need you', D.needs.length], ['Sites to sign up', D.signups.length], ['Duplicates skipped', S.duplicates_skipped]
].map(([l, v]) => `<div class="stat"><b>${v ?? 0}</b><span>${l}</span></div>`).join('');

const TABS = [['applied','Applied',D.applied.length],['needs','Needs you',D.needs.length],['signup','Sign up',D.signups.length],['asks','What they ask for',D.gaps.length],['content','Content to make',D.ideas.length]];
const nav = document.getElementById('tabs');
nav.innerHTML = TABS.map(([id, l, n]) => `<button data-t="${id}">${l}<span class="n">${n}</span></button>`).join('');
function show(id){
  document.querySelectorAll('nav button').forEach(b => b.classList.toggle('on', b.dataset.t === id));
  document.querySelectorAll('section').forEach(s => s.classList.toggle('on', s.id === id));
  try { localStorage.setItem('tab', id); } catch {}
}
nav.addEventListener('click', e => { const b = e.target.closest('button'); if (b) show(b.dataset.t); });
let start = 'applied'; try { start = localStorage.getItem('tab') || (D.applied.length ? 'applied' : 'needs'); } catch {}
show(start);

function prep(raw){
  let a = []; try { a = JSON.parse(raw || '[]'); } catch {}
  if (!a.length) return '<span class="sub">No answer log</span>';
  const st = a.filter(x => x.stretch);
  return `<details${st.length ? ' open' : ''}><summary>${a.length} answers, <b>${st.length} stretched</b></summary>` +
    st.map(x => `<div class="stretch"><div class="sub">${esc(x.q)}</div><b>You said:</b> ${esc(x.a)}<br><b>Back it up:</b> ${esc(x.prep)}</div>`).join('') +
    a.filter(x => !x.stretch).map(x => `<div class="ans"><div class="q">${esc(x.q)}</div>${esc(x.a)}</div>`).join('') + `</details>`;
}
function jobsTable(){
  const which = document.getElementById('which').value, q = document.getElementById('q').value.toLowerCase();
  const rows = (which === 'applied' ? D.applied : D.scored).filter(j =>
    !q || [j.company, j.site, j.title, j.company_country, j.location].join(' ').toLowerCase().includes(q));
  const el = document.getElementById('appliedTable');
  if (!rows.length) { el.innerHTML = `<div class="empty">${which === 'applied' ? 'Nothing submitted yet. Jobs show up here after a real (not dry-run) apply.' : 'No scored jobs match.'}</div>`; return; }
  el.innerHTML = `<div class="tablewrap"><table><thead><tr><th>${which==='applied'?'Applied':'Found'}</th><th>Company</th><th>Country</th><th>Role</th><th>Fit</th><th>What they ask for</th><th>You're missing</th><th>Interview prep</th><th>Status</th></tr></thead><tbody>` +
    rows.map(j => `<tr>
      <td class="mono">${esc((j.applied_at || j.discovered_at || '').slice(0,10))}</td>
      <td><b>${esc(j.company || j.site)}</b><div class="sub">via ${esc(j.site)}</div></td>
      <td>${esc(j.company_country || j.location || '')}</td>
      <td><a href="${esc(j.application_url || j.url)}" target="_blank" rel="noopener">${esc(j.title)}</a>${j.salary ? `<div class="sub">${esc(j.salary)}</div>` : ''}</td>
      <td class="fit ${fitCls(j.fit_score)}">${j.fit_score ?? ''}</td>
      <td><div class="tags">${split(j.requirements, ';').map(r => `<span class="tag">${esc(r)}</span>`).join('')}</div></td>
      <td><div class="tags">${split(j.missing_skills, ',').slice(0,6).map(r => `<span class="tag miss">${esc(r)}</span>`).join('')}</div></td>
      <td style="min-width:240px">${prep(j.submitted_answers)}</td>
      <td><span class="pill">${esc((j.apply_status || 'not tried').replace(/_/g,' '))}</span></td>
    </tr>`).join('') + `</tbody></table></div>`;
}
document.getElementById('q').addEventListener('input', jobsTable);
document.getElementById('which').addEventListener('change', jobsTable);
jobsTable();

document.getElementById('needsList').innerHTML = D.needs.length ? D.needs.map(j => {
  const k = j.kit, link = j.application_url || j.url;
  let body = `<div class="row"><a class="copy" href="${esc(link)}" target="_blank" rel="noopener">Open application</a>${copyBtn(link)}</div>`;
  if (j.apply_notes) body += `<details><summary>Why the bot stopped</summary><div class="why">${esc(j.apply_notes)}</div></details>`;
  if (!k) body += `<p class="note" style="margin-top:10px">Kit not built yet. Run <span class="mono">applypilot kits</span>.</p>`;
  else {
    if (k.you_must_answer && k.you_must_answer.length) body += `<div class="must"><b>Only you can answer:</b><br>${k.you_must_answer.map(esc).join('<br>')}</div>`;
    if (k.resume_pdf) body += `<div class="row"><span class="sub">Resume to upload:</span><span class="mono">${esc(k.resume_pdf)}</span>${copyBtn(k.resume_pdf)}</div>`;
    if (k.cover_letter) body += `<details open><summary>Cover letter</summary><div class="row" style="justify-content:flex-end">${copyBtn(k.cover_letter)}</div><div class="big">${esc(k.cover_letter)}</div></details>`;
    body += `<details open><summary>Form answers</summary><div class="qa">${k.answers.map(a => `<div class="q">${esc(a.q)}</div><div class="a">${esc(a.a)}</div>${copyBtn(a.a)}`).join('')}</div></details>`;
  }
  return `<div class="card"><h3>${esc(j.title)}</h3><div class="meta">${esc(j.company || j.site)} · ${esc(j.company_country || '')} · fit <span class="fit ${fitCls(j.fit_score)}">${j.fit_score}</span> · stopped: ${esc((j.apply_error || '').replace(/_/g,' '))}</div>${body}</div>`;
}).join('') : '<div class="empty">Nothing waiting on you.</div>';

document.getElementById('signupList').innerHTML = D.signups.length ? `<div class="tablewrap"><table><thead><tr><th>Site</th><th>Jobs waiting</th><th>Best fit</th><th>Sign up</th><th>Jobs</th></tr></thead><tbody>` +
  D.signups.map(s => `<tr><td><b>${esc(s.domain)}</b></td><td>${s.jobs.length}</td><td class="fit ${fitCls(s.best_fit)}">${s.best_fit}</td>
    <td><a href="${esc(s.signup_url)}" target="_blank" rel="noopener">${esc(s.signup_url)}</a></td>
    <td>${s.jobs.map(j => `${j.fit}/10 <a href="${esc(j.url)}" target="_blank" rel="noopener">${esc(j.title)}</a> at ${esc(j.company)}`).join('<br>')}</td></tr>`).join('') +
  `</tbody></table></div>` : '<div class="empty">No sites waiting for a sign-up.</div>';

const maxJobs = Math.max(1, ...D.gaps.map(g => g.jobs));
document.getElementById('gapTable').innerHTML = D.gaps.length ? `<div class="tablewrap"><table><thead><tr><th>#</th><th>Skill</th><th>Jobs</th><th style="width:30%">Share</th><th>Avg fit</th><th>Status</th><th>Seen in</th></tr></thead><tbody>` +
  D.gaps.map((g, i) => `<tr><td class="mono">${i+1}</td><td><b>${esc(g.skill)}</b></td><td class="mono">${g.jobs}</td>
    <td><div class="bar" style="width:${Math.round(g.jobs/maxJobs*100)}%"></div><span class="sub">${Math.round(g.share*100)}%</span></td>
    <td class="mono">${g.avg_fit.toFixed(1)}</td><td><span class="pill">${esc(g.status)}</span>${g.note ? `<div class="sub">${esc(g.note)}</div>` : ''}</td>
    <td class="sub">${g.examples.map(esc).join('<br>')}</td></tr>`).join('') + `</tbody></table></div>` : '<div class="empty">No gaps yet. Run: applypilot run score</div>';

document.getElementById('ideas').innerHTML = D.ideas.length ? D.ideas.map(i => `<div class="card">
  <span class="pill">${esc(i.format)}</span>
  <h3 style="margin-top:8px">${esc(i.title)}</h3>
  <div class="big" style="margin:8px 0">${esc(i.hook)}</div>
  <div class="row" style="justify-content:flex-end;margin-top:0">${copyBtn(i.hook)}</div>
  <p style="font-size:13.5px">${esc(i.what_to_show)}</p>
  <div class="tags">${split(i.skills, ',').map(s => `<span class="tag miss">${esc(s)}</span>`).join('')}</div>
  <p class="sub" style="margin:10px 0 0"><b>Speaks to:</b> ${esc(i.companies)}</p>
  <p class="sub" style="margin:4px 0 0">${esc(i.why)}</p>
</div>`).join('') : '<div class="empty">No ideas yet. Run: applypilot content</div>';
</script>
</body>
</html>
"""
