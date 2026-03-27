"""
web/app.py — FastAPI dashboard
Fix #7: Proper login page with session cookie instead of insecure prompt()
"""
import secrets
from typing import Optional

from fastapi import FastAPI, Request, Response, Depends, HTTPException, status, Cookie
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from config import DASHBOARD_TOKEN
from database import users as users_db, bots as bots_db, broadcasts as bc_db
from utils.importer import import_from_mongo
from utils.broadcaster import request_cancel

app = FastAPI(title="MultiBot Dashboard", version="2.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# Fix #7: in-memory session store (token → session_id)
_SESSIONS: dict = {}


def _make_session() -> str:
    sid = secrets.token_urlsafe(32)
    _SESSIONS[sid] = True
    return sid


def _check_session(session: Optional[str] = Cookie(default=None)):
    if not session or session not in _SESSIONS:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    return True


# ── Auth ──────────────────────────────────────────────────────────────────────

@app.get("/login", response_class=HTMLResponse)
async def login_page():
    return HTMLResponse(LOGIN_HTML)


@app.post("/login")
async def do_login(request: Request, response: Response):
    form = await request.form()
    token = form.get("token", "")
    if token != DASHBOARD_TOKEN:
        return HTMLResponse(LOGIN_HTML.replace("<!--ERR-->",
            '<p style="color:#ef4444;margin-top:8px">❌ Invalid token</p>'))
    sid = _make_session()
    resp = RedirectResponse("/", status_code=302)
    resp.set_cookie("session", sid, httponly=True, samesite="lax", max_age=86400)
    return resp


@app.post("/logout")
async def logout(response: Response, session: Optional[str] = Cookie(default=None)):
    if session:
        _SESSIONS.pop(session, None)
    resp = RedirectResponse("/login", status_code=302)
    resp.delete_cookie("session")
    return resp


# ── Dashboard ─────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def dashboard(session: Optional[str] = Cookie(default=None)):
    if not session or session not in _SESSIONS:
        return RedirectResponse("/login")
    return HTMLResponse(DASHBOARD_HTML)


# ── API routes (all require session) ─────────────────────────────────────────

@app.get("/api/stats")
async def api_stats(_=Depends(_check_session)):
    g    = await users_db.global_stats()
    bots = await bots_db.get_all_bots()
    return {"global": g, "bot_count": len(bots)}


@app.get("/api/bots")
async def api_bots(_=Depends(_check_session)):
    from datetime import datetime, timezone
    from config import HEARTBEAT_TIMEOUT
    bots   = await bots_db.get_all_bots()
    result = []
    for b in bots:
        last = b.get("last_seen")
        if last:
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            online = (datetime.now(timezone.utc) - last).total_seconds() < HEARTBEAT_TIMEOUT
        else:
            online = False
        s = await users_db.stats_for_bot(b["bot_id"])
        result.append({
            "bot_id":   b["bot_id"],
            "bot_name": b.get("bot_name", str(b["bot_id"])),
            "is_active": b.get("is_active"),
            "online":   online,
            "status":   b.get("status", "unknown"),
            **s,
        })
    return result


class AddBotReq(BaseModel):
    token: str

@app.post("/api/bots")
async def api_add_bot(req: AddBotReq, _=Depends(_check_session)):
    from bot_manager import manager
    try:
        info = await manager.add_bot(req.token)
        return {"ok": True, **info}
    except Exception as e:
        raise HTTPException(400, str(e))


@app.delete("/api/bots/{bot_id}")
async def api_remove_bot(bot_id: int, _=Depends(_check_session)):
    from bot_manager import manager
    await manager.remove_bot(bot_id)
    await bots_db.remove_bot(bot_id)
    return {"ok": True}


@app.post("/api/bots/{bot_id}/close")
async def api_close(bot_id: int, _=Depends(_check_session)):
    count = await users_db.close_bot_users(bot_id)
    return {"ok": True, "closed": count}


@app.post("/api/bots/{bot_id}/open")
async def api_open(bot_id: int, _=Depends(_check_session)):
    count = await users_db.open_bot_users(bot_id)
    return {"ok": True, "opened": count}


@app.get("/api/broadcasts")
async def api_broadcasts(_=Depends(_check_session)):
    return await bc_db.get_recent_broadcasts(20)


@app.post("/api/broadcasts/cancel/{bc_id}")
async def api_cancel(bc_id: str, _=Depends(_check_session)):
    request_cancel(bc_id)
    await bc_db.cancel_broadcast(bc_id)
    return {"ok": True}


@app.get("/api/analytics")
async def api_analytics(bot_id: Optional[int] = None, _=Depends(_check_session)):
    growth = await users_db.daily_growth(bot_id=bot_id, days=14)
    return {"growth": growth}


class ImportReq(BaseModel):
    mongo_url: str
    db_name: str
    collection: str
    bot_id: int

@app.post("/api/import")
async def api_import(req: ImportReq, _=Depends(_check_session)):
    ins, skp, err = await import_from_mongo(
        req.mongo_url, req.db_name, req.collection, req.bot_id
    )
    if err:
        raise HTTPException(400, err)
    return {"ok": True, "inserted": ins, "skipped": skp}


@app.get("/api/templates")
async def api_templates(_=Depends(_check_session)):
    return await bc_db.get_templates()


@app.get("/api/schedules")
async def api_schedules(_=Depends(_check_session)):
    return await bc_db.get_pending_schedules()


# ── HTML ──────────────────────────────────────────────────────────────────────

LOGIN_HTML = """<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>MultiBot Login</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:#0f1117;display:flex;align-items:center;justify-content:center;min-height:100vh;font-family:'Segoe UI',sans-serif}
  .card{background:#1a1d27;border:1px solid #2d3148;border-radius:16px;padding:40px;width:360px;text-align:center}
  h2{color:#e2e8f0;margin-bottom:8px}
  p{color:#64748b;font-size:.9rem;margin-bottom:24px}
  input{width:100%;background:#0f1117;border:1px solid #2d3148;color:#e2e8f0;border-radius:8px;padding:12px 14px;font-size:.95rem;margin-bottom:16px}
  button{width:100%;background:#7c6af7;color:#fff;border:none;border-radius:8px;padding:12px;font-size:1rem;cursor:pointer}
  button:hover{opacity:.85}
  <!--ERR-->
</style></head>
<body><div class="card">
  <h2>🤖 MultiBot</h2>
  <p>Enter your dashboard token to continue</p>
  <form method="post" action="/login">
    <input type="password" name="token" placeholder="Dashboard token" autofocus>
    <button type="submit">Login →</button>
  </form>
  <!--ERR-->
</div></body></html>"""


DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>MultiBot Dashboard</title>
<style>
  :root{--bg:#0f1117;--card:#1a1d27;--accent:#7c6af7;--green:#22c55e;--red:#ef4444;--yellow:#f59e0b;--text:#e2e8f0;--muted:#64748b;--border:#2d3148}
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:var(--bg);color:var(--text);font-family:'Segoe UI',sans-serif;min-height:100vh}
  header{background:var(--card);padding:14px 28px;display:flex;align-items:center;gap:12px;border-bottom:1px solid var(--border)}
  header h1{font-size:1.2rem;font-weight:700}
  .badge{background:var(--accent);color:#fff;border-radius:99px;padding:2px 10px;font-size:.75rem}
  .logout{margin-left:auto;background:none;border:1px solid var(--border);color:var(--muted);padding:6px 14px;border-radius:6px;cursor:pointer;font-size:.8rem}
  nav{display:flex;gap:4px;padding:14px 28px;border-bottom:1px solid var(--border);flex-wrap:wrap}
  nav button{background:none;border:none;color:var(--muted);padding:7px 14px;border-radius:7px;cursor:pointer;font-size:.88rem}
  nav button.active{background:var(--accent);color:#fff}
  main{padding:22px 28px;max-width:1200px}
  .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:14px;margin-bottom:22px}
  .card{background:var(--card);border-radius:12px;padding:18px;border:1px solid var(--border)}
  .card .val{font-size:1.9rem;font-weight:700;color:var(--accent)}
  .card .lbl{color:var(--muted);font-size:.82rem;margin-top:4px}
  table{width:100%;border-collapse:collapse}
  th{text-align:left;color:var(--muted);font-size:.78rem;padding:8px 10px;border-bottom:1px solid var(--border);text-transform:uppercase}
  td{padding:9px 10px;border-bottom:1px solid #1e2130;font-size:.88rem;vertical-align:middle}
  .online{color:var(--green)}.offline{color:var(--red)}
  input,select,textarea{background:#0f1117;border:1px solid var(--border);color:var(--text);border-radius:8px;padding:9px 12px;width:100%;margin-bottom:10px;font-size:.88rem}
  .btn{background:var(--accent);color:#fff;border:none;border-radius:7px;padding:8px 16px;cursor:pointer;font-size:.85rem;white-space:nowrap}
  .btn:hover{opacity:.85}.btn.sm{padding:4px 10px;font-size:.78rem}
  .btn.red{background:var(--red)}.btn.green{background:var(--green)}
  .section{display:none}.section.active{display:block}
  .row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
  #toast{position:fixed;bottom:20px;right:20px;padding:11px 18px;border-radius:8px;display:none;z-index:999;font-size:.88rem}
  .progress-wrap{height:8px;background:var(--border);border-radius:4px;margin-top:8px}
  .progress-bar{height:100%;background:var(--accent);border-radius:4px;width:0%;transition:.4s}
  label.chk{display:flex;align-items:center;gap:6px;color:var(--muted);font-size:.85rem;cursor:pointer}
  code{background:#1e2130;padding:2px 6px;border-radius:4px;font-size:.82rem}
</style></head>
<body>
<header>
  <h1>🤖 MultiBot</h1><span class="badge">v2</span>
  <span id="lu" style="margin-left:16px;color:var(--muted);font-size:.8rem"></span>
  <form method="post" action="/logout" style="margin-left:auto">
    <button class="logout" type="submit">Logout</button>
  </form>
</header>
<nav>
  <button class="active" onclick="show('overview',this)">📊 Overview</button>
  <button onclick="show('bots',this)">🤖 Bots</button>
  <button onclick="show('broadcast',this)">📢 Broadcast</button>
  <button onclick="show('schedule',this)">⏰ Schedule</button>
  <button onclick="show('logs',this)">📜 Logs</button>
  <button onclick="show('analytics',this)">📈 Analytics</button>
  <button onclick="show('import',this)">📥 Import</button>
</nav>
<main>

<!-- OVERVIEW -->
<div class="section active" id="overview">
  <div class="grid" id="statCards"></div>
  <div class="card">
    <table><thead><tr><th>Bot</th><th>Status</th><th>Total</th><th>Eligible</th><th>Blocked</th><th>Closed</th><th>Imported</th></tr></thead>
    <tbody id="overviewTable"></tbody></table>
  </div>
</div>

<!-- BOTS -->
<div class="section" id="bots">
  <div class="card" style="margin-bottom:14px">
    <h3 style="margin-bottom:12px;font-size:1rem">➕ Add Bot</h3>
    <div class="row">
      <input id="newToken" placeholder="Bot token from @BotFather" style="margin:0;flex:1">
      <button class="btn" onclick="addBot()">Add</button>
    </div>
  </div>
  <div class="card">
    <table><thead><tr><th>Bot</th><th>Status</th><th>Users</th><th>Eligible</th><th>Actions</th></tr></thead>
    <tbody id="botsTable"></tbody></table>
  </div>
</div>

<!-- BROADCAST -->
<div class="section" id="broadcast">
  <div class="card">
    <h3 style="margin-bottom:12px;font-size:1rem">📢 Send Broadcast</h3>
    <label style="color:var(--muted);font-size:.82rem;display:block;margin-bottom:6px">Target</label>
    <select id="bcBot"><option value="">— All Bots —</option></select>
    <textarea id="bcText" rows="4" placeholder="Message text..."></textarea>
    <div class="row">
      <label class="chk"><input type="checkbox" id="bcPin"> Pin message</label>
      <button class="btn" style="margin-left:auto" onclick="sendBc()">🚀 Broadcast</button>
    </div>
    <p style="color:var(--muted);font-size:.78rem;margin-top:8px">
      💡 Tip: For images/media, use /broadcast from Telegram (reply to media message)
    </p>
  </div>
</div>

<!-- SCHEDULE -->
<div class="section" id="schedule">
  <div class="card" style="margin-bottom:14px">
    <h3 style="margin-bottom:12px;font-size:1rem">⏰ Schedule Broadcast</h3>
    <select id="schBot"><option value="">— All Bots —</option></select>
    <textarea id="schText" rows="3" placeholder="Scheduled message text..."></textarea>
    <div class="row">
      <input id="schDate" type="date" style="margin:0;flex:1">
      <input id="schTime" type="time" style="margin:0;flex:1">
      <button class="btn" onclick="createSchedule()">Schedule</button>
    </div>
  </div>
  <div class="card">
    <h3 style="margin-bottom:12px;font-size:1rem">📋 Pending Schedules</h3>
    <table><thead><tr><th>ID</th><th>Target</th><th>Run At (UTC)</th><th>Preview</th></tr></thead>
    <tbody id="schedTable"></tbody></table>
  </div>
</div>

<!-- LOGS -->
<div class="section" id="logs">
  <div class="card">
    <table><thead><tr><th>ID</th><th>Target</th><th>Total</th><th>✅</th><th>❌</th><th>Status</th><th>When</th></tr></thead>
    <tbody id="logsTable"></tbody></table>
  </div>
</div>

<!-- ANALYTICS -->
<div class="section" id="analytics">
  <div class="card">
    <h3 style="margin-bottom:12px;font-size:1rem">📈 User Growth (last 14 days)</h3>
    <select id="analyticsBot" onchange="loadAnalytics()"><option value="">All Bots</option></select>
    <canvas id="growthChart" height="120" style="margin-top:16px"></canvas>
  </div>
</div>

<!-- IMPORT -->
<div class="section" id="import">
  <div class="card">
    <h3 style="margin-bottom:12px;font-size:1rem">📥 Import from External MongoDB</h3>
    <input id="impUrl" placeholder="mongodb+srv://user:pass@host/...">
    <div class="row">
      <input id="impDb" placeholder="Database name" style="margin:0;flex:1">
      <input id="impCol" placeholder="Collection name" style="margin:0;flex:1">
    </div>
    <input id="impBot" placeholder="Target bot_id" style="margin-top:10px">
    <button class="btn" onclick="runImport()">Import</button>
    <p id="impResult" style="margin-top:10px;font-size:.85rem"></p>
  </div>
</div>

</main>
<div id="toast"></div>

<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<script>
let _chart = null;

async function api(path, method='GET', body=null){
  const r = await fetch('/api'+path,{
    method, headers:{'Content-Type':'application/json'},
    body: body ? JSON.stringify(body) : null,
    credentials: 'same-origin'
  });
  if(r.status===401){ location='/login'; return null; }
  return r.json();
}

function show(id, btn){
  document.querySelectorAll('.section').forEach(s=>s.classList.remove('active'));
  document.querySelectorAll('nav button').forEach(b=>b.classList.remove('active'));
  document.getElementById(id).classList.add('active');
  if(btn) btn.classList.add('active');
  if(id==='overview')   loadOverview();
  if(id==='bots')       loadBots();
  if(id==='logs')       loadLogs();
  if(id==='analytics')  loadAnalytics();
  if(id==='broadcast')  loadBcBots();
  if(id==='schedule')   { loadSchBots(); loadSchedules(); }
}

function toast(msg, ok=true){
  const t=document.getElementById('toast');
  t.textContent=msg; t.style.background=ok?'#22c55e':'#ef4444';
  t.style.display='block'; setTimeout(()=>t.style.display='none',3000);
}

async function loadOverview(){
  const [stats, bots] = await Promise.all([api('/stats'), api('/bots')]);
  if(!stats||!bots) return;
  const g = stats.global;
  document.getElementById('statCards').innerHTML = `
    <div class="card"><div class="val">${g.total}</div><div class="lbl">Total Users</div></div>
    <div class="card"><div class="val">${g.active}</div><div class="lbl">Active</div></div>
    <div class="card"><div class="val">${g.eligible}</div><div class="lbl">Eligible</div></div>
    <div class="card"><div class="val">${g.blocked}</div><div class="lbl">Blocked</div></div>
    <div class="card"><div class="val">${stats.bot_count}</div><div class="lbl">Bots</div></div>`;
  document.getElementById('overviewTable').innerHTML = bots.map(b=>`
    <tr>
      <td>@${b.bot_name} <code>${b.bot_id}</code></td>
      <td class="${b.online?'online':'offline'}">${b.online?'🟢 Online':'🔴 Offline'}</td>
      <td>${b.total}</td><td>${b.eligible}</td><td>${b.blocked}</td><td>${b.closed}</td><td>${b.imported}</td>
    </tr>`).join('');
  document.getElementById('lu').textContent = 'Updated: '+new Date().toLocaleTimeString();
}

async function loadBots(){
  const bots = await api('/bots');
  if(!bots) return;
  document.getElementById('botsTable').innerHTML = bots.map(b=>`
    <tr>
      <td>@${b.bot_name} <code>${b.bot_id}</code></td>
      <td class="${b.online?'online':'offline'}">${b.online?'🟢':'🔴'} ${b.status}</td>
      <td>${b.total}</td><td>${b.eligible}</td>
      <td>
        <button class="btn sm" onclick="closeUsers(${b.bot_id})">🔒</button>
        <button class="btn sm green" onclick="openUsers(${b.bot_id})">🔓</button>
        <button class="btn sm red" onclick="removeBot(${b.bot_id})">🗑</button>
      </td>
    </tr>`).join('');
}

async function addBot(){
  const token = document.getElementById('newToken').value.trim();
  if(!token) return toast('Enter token',false);
  const r = await api('/bots','POST',{token});
  if(!r) return;
  if(r.ok){ toast('✅ @'+r.username+' added'); document.getElementById('newToken').value=''; loadBots(); }
  else toast(r.detail||'Failed',false);
}

async function removeBot(id){
  if(!confirm('Remove bot '+id+'?')) return;
  await api('/bots/'+id,'DELETE'); toast('Removed'); loadBots();
}

async function closeUsers(id){ const r=await api('/bots/'+id+'/close','POST'); toast('🔒 Closed '+r.closed); }
async function openUsers(id){ const r=await api('/bots/'+id+'/open','POST'); toast('🔓 Opened '+r.opened); }

async function loadBcBots(){
  const bots = await api('/bots');
  if(!bots) return;
  const sel = document.getElementById('bcBot');
  sel.innerHTML = '<option value="">— All Bots —</option>';
  bots.forEach(b => sel.innerHTML += `<option value="${b.bot_id}">@${b.bot_name}</option>`);
}

async function sendBc(){
  toast('💡 Use /broadcast in Telegram for media. Text-only here.',false);
}

async function loadSchBots(){
  const bots = await api('/bots');
  if(!bots) return;
  const sel = document.getElementById('schBot');
  sel.innerHTML = '<option value="">— All Bots —</option>';
  bots.forEach(b => sel.innerHTML += `<option value="${b.bot_id}">@${b.bot_name}</option>`);
}

async function createSchedule(){
  toast('Use /schedule command in Telegram for full schedule support',false);
}

async function loadSchedules(){
  const data = await api('/schedules');
  if(!data) return;
  document.getElementById('schedTable').innerHTML = data.length ? data.map(s=>`
    <tr>
      <td><code>${s._id.slice(-6)}</code></td>
      <td>${s.target_bot_id||'All'}</td>
      <td>${new Date(s.run_at).toLocaleString()}</td>
      <td>${(s.text||'').slice(0,40)}</td>
    </tr>`).join('') : '<tr><td colspan="4" style="color:var(--muted);text-align:center;padding:20px">No pending schedules</td></tr>';
}

async function loadLogs(){
  const logs = await api('/broadcasts');
  if(!logs) return;
  const em = {completed:'✅',running:'🔄',cancelled:'🛑',saved:'💾'};
  document.getElementById('logsTable').innerHTML = logs.map(d=>`
    <tr>
      <td><code>${d._id.slice(-8)}</code></td>
      <td>${d.target_bot_id||'All'}</td>
      <td>${d.total_users}</td>
      <td style="color:var(--green)">${d.success}</td>
      <td style="color:var(--red)">${d.failed}</td>
      <td>${em[d.status]||'?'} ${d.status}
        ${d.status==='running'?`<button class="btn sm red" onclick="cancelBc('${d._id}')">Cancel</button>`:''}
      </td>
      <td style="color:var(--muted);font-size:.78rem">${new Date(d.created_at).toLocaleString()}</td>
    </tr>`).join('');
}

async function cancelBc(id){ await api('/broadcasts/cancel/'+id,'POST'); toast('Cancelled'); loadLogs(); }

async function loadAnalytics(){
  const botId = document.getElementById('analyticsBot').value;
  const path  = '/analytics' + (botId ? '?bot_id='+botId : '');
  const data  = await api(path);
  if(!data) return;

  const labels = data.growth.map(d=>d.date);
  const vals   = data.growth.map(d=>d.count);

  if(_chart) _chart.destroy();
  _chart = new Chart(document.getElementById('growthChart'),{
    type:'bar',
    data:{labels, datasets:[{label:'New Users',data:vals,backgroundColor:'#7c6af7',borderRadius:6}]},
    options:{plugins:{legend:{display:false}},scales:{y:{beginAtZero:true,grid:{color:'#1e2130'}},x:{grid:{display:false}}}}
  });

  // Populate bot selector
  const bots = await api('/bots');
  if(!bots) return;
  const sel = document.getElementById('analyticsBot');
  if(sel.options.length===1){
    bots.forEach(b=>sel.innerHTML+=`<option value="${b.bot_id}">@${b.bot_name}</option>`);
  }
}

async function runImport(){
  const body={
    mongo_url: document.getElementById('impUrl').value,
    db_name:   document.getElementById('impDb').value,
    collection:document.getElementById('impCol').value,
    bot_id:    parseInt(document.getElementById('impBot').value)||0,
  };
  const p = document.getElementById('impResult');
  p.style.color='var(--muted)'; p.textContent='⏳ Importing...';
  const r = await api('/import','POST',body);
  if(!r) return;
  if(r.ok){ p.style.color='var(--green)'; p.textContent=`✅ Inserted: ${r.inserted} | Skipped: ${r.skipped}`; }
  else{ p.style.color='var(--red)'; p.textContent='❌ '+r.detail; }
}

// Auto-refresh every 30s
loadOverview();
setInterval(()=>{
  if(document.getElementById('overview').classList.contains('active')) loadOverview();
  if(document.getElementById('logs').classList.contains('active')) loadLogs();
},30000);
</script>
</body></html>"""
