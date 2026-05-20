"""
SynAgent Reaction Database — USB Desktop App
=============================================
Double-click to launch. Opens automatically in your browser.
Works on any Windows laptop. No installation needed.

Architecture:
  - This file = entry point (tkinter status window + FastAPI server + auto browser open)
  - DB auto-discovered relative to the EXE location
  - PyInstaller bundles everything into one .exe
"""
from __future__ import annotations

import io
import json
import os
import sqlite3
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import Optional

# ── Path detection (works frozen as .exe OR as plain .py) ───────────────────
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).parent          # D:\SynAgent\
else:
    BASE_DIR = Path(__file__).parent.parent.parent  # repo root — override below

# Allow env var override, then fall back to sibling db/ folder
_env = os.environ.get("PATENT_DATA_DIR")
DB_PATH: Optional[Path] = None

def _find_db() -> Optional[Path]:
    global DB_PATH
    if DB_PATH and DB_PATH.exists():
        return DB_PATH
    candidates = [
        _env,
        str(BASE_DIR),
        r"D:\SynAgent", r"E:\SynAgent", r"F:\SynAgent", r"G:\SynAgent",
        str(Path(__file__).parent.parent / "patent_ingestion_pipeline" / "data"),
    ]
    for c in candidates:
        if c is None:
            continue
        p = Path(c) / "db" / "patent_pipeline.db"
        if p.exists():
            DB_PATH = p
            return p
    return None

# ── FastAPI app ──────────────────────────────────────────────────────────────
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

app = FastAPI(title="SynAgent Reaction DB")

def _conn():
    db = _find_db()
    if db is None:
        raise RuntimeError("Database not found.")
    c = sqlite3.connect(str(db))
    c.row_factory = sqlite3.Row
    return c

# ── API ──────────────────────────────────────────────────────────────────────

@app.get("/api/stats")
def api_stats():
    try:
        c = _conn()
        total    = c.execute("SELECT COUNT(*) FROM reactions").fetchone()[0]
        ord_c    = c.execute("SELECT COUNT(*) FROM reactions WHERE source='ord'").fetchone()[0]
        patent_c = c.execute("SELECT COUNT(*) FROM reactions WHERE source='patent'").fetchone()[0]
        hi       = c.execute("SELECT COUNT(*) FROM reactions WHERE confidence>=0.6").fetchone()[0]
        yld      = c.execute("SELECT COUNT(*) FROM reactions WHERE yield_percent IS NOT NULL").fetchone()[0]
        avg_yld  = c.execute("SELECT AVG(yield_percent) FROM reactions WHERE yield_percent IS NOT NULL").fetchone()[0]
        solvents = c.execute("SELECT COUNT(DISTINCT solvent) FROM reactions WHERE solvent IS NOT NULL AND solvent!=''").fetchone()[0]
        cats     = c.execute("SELECT COUNT(DISTINCT catalyst) FROM reactions WHERE catalyst IS NOT NULL AND catalyst!=''").fetchone()[0]
        c.close()
        return dict(total=total, ord=ord_c, patent=patent_c,
                    high_confidence=hi, with_yield=yld,
                    avg_yield=round(avg_yld, 1) if avg_yld else None,
                    distinct_solvents=solvents, distinct_catalysts=cats,
                    db_path=str(_find_db()))
    except Exception as e:
        return JSONResponse({"error": str(e)}, 500)

@app.get("/api/search")
def api_search(
    q: str = Query(""),
    source: str = Query("all"),
    min_yield: Optional[float] = Query(None),
    max_yield: Optional[float] = Query(None),
    min_confidence: float = Query(0.0),
    solvent: str = Query(""),
    catalyst: str = Query(""),
    sort: str = Query("confidence"),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
):
    try:
        c = _conn()
        params: list = []
        conds: list[str] = []

        if q.strip():
            try:
                fts = c.execute(
                    "SELECT reaction_id FROM reactions_fts WHERE reactions_fts MATCH ? LIMIT 5000",
                    (q.strip(),)
                ).fetchall()
                ids = [r[0] for r in fts]
                if ids:
                    conds.append(f"r.reaction_id IN ({','.join('?'*len(ids))})")
                    params.extend(ids)
                else:
                    like = f"%{q.strip()[:60]}%"
                    conds.append("(r.reaction_smarts LIKE ? OR r.product_smiles LIKE ? OR r.solvent LIKE ? OR r.catalyst LIKE ? OR r.notes LIKE ? OR r.mechanism_text LIKE ?)")
                    params.extend([like]*6)
            except sqlite3.OperationalError:
                like = f"%{q.strip()[:60]}%"
                conds.append("(r.reaction_smarts LIKE ? OR r.product_smiles LIKE ? OR r.solvent LIKE ? OR r.catalyst LIKE ?)")
                params.extend([like]*4)

        if source in ("ord","patent"):
            conds.append("r.source=?"); params.append(source)
        if min_yield is not None:
            conds.append("r.yield_percent>=?"); params.append(min_yield)
        if max_yield is not None:
            conds.append("r.yield_percent<=?"); params.append(max_yield)
        if min_confidence > 0:
            conds.append("r.confidence>=?"); params.append(min_confidence)
        if solvent.strip():
            conds.append("r.solvent LIKE ?"); params.append(f"%{solvent.strip()}%")
        if catalyst.strip():
            conds.append("r.catalyst LIKE ?"); params.append(f"%{catalyst.strip()}%")

        where = ("WHERE " + " AND ".join(conds)) if conds else ""
        sort_map = {"confidence":"r.confidence DESC","yield":"r.yield_percent DESC NULLS LAST","temperature":"r.temperature_celsius ASC NULLS LAST","id":"r.rowid ASC"}
        order = sort_map.get(sort, "r.confidence DESC")

        total = c.execute(f"SELECT COUNT(*) FROM reactions r {where}", params).fetchone()[0]
        offset = (page-1)*page_size
        rows = c.execute(f"SELECT r.* FROM reactions r {where} ORDER BY {order} LIMIT ? OFFSET ?",
                         params+[page_size, offset]).fetchall()
        c.close()

        results = []
        for row in rows:
            d = dict(row)
            try: d["reactants"] = json.loads(d.get("reactant_smiles_json") or "[]")
            except: d["reactants"] = []
            results.append(d)

        return dict(total=total, page=page, page_size=page_size,
                    total_pages=max(1,(total+page_size-1)//page_size),
                    results=results)
    except Exception as e:
        return JSONResponse({"error": str(e)}, 500)

@app.get("/api/reaction/{rid}")
def api_reaction(rid: str):
    try:
        c = _conn()
        row = c.execute("SELECT * FROM reactions WHERE reaction_id=?", (rid,)).fetchone()
        c.close()
        if not row: return JSONResponse({"error":"Not found"},404)
        d = dict(row)
        try: d["reactants"] = json.loads(d.get("reactant_smiles_json") or "[]")
        except: d["reactants"] = []
        return d
    except Exception as e:
        return JSONResponse({"error":str(e)},500)

@app.get("/api/export.csv")
def api_export(q: str = Query(""), source: str = Query("all"),
               min_yield: Optional[float] = Query(None),
               min_confidence: float = Query(0.0)):
    try:
        c = _conn()
        conds, params = [], []
        if q.strip():
            try:
                fts = c.execute("SELECT reaction_id FROM reactions_fts WHERE reactions_fts MATCH ? LIMIT 10000",(q.strip(),)).fetchall()
                ids = [r[0] for r in fts]
                if ids:
                    conds.append(f"reaction_id IN ({','.join('?'*len(ids))})"); params.extend(ids)
            except: pass
        if source in ("ord","patent"):
            conds.append("source=?"); params.append(source)
        if min_yield is not None:
            conds.append("yield_percent>=?"); params.append(min_yield)
        if min_confidence > 0:
            conds.append("confidence>=?"); params.append(min_confidence)
        where = ("WHERE " + " AND ".join(conds)) if conds else ""
        rows = c.execute(f"SELECT reaction_id,source,confidence,reaction_smarts,product_smiles,yield_percent,temperature_celsius,solvent,catalyst,notes FROM reactions {where} ORDER BY confidence DESC LIMIT 10000", params).fetchall()
        c.close()

        buf = io.StringIO()
        buf.write("reaction_id,source,confidence,reaction_smarts,product_smiles,yield_percent,temperature_celsius,solvent,catalyst,notes\n")
        for row in rows:
            buf.write(",".join(f'"{str(v).replace(chr(34),chr(39))}"' if v is not None else "" for v in row)+"\n")
        buf.seek(0)
        return StreamingResponse(iter([buf.getvalue()]),media_type="text/csv",
            headers={"Content-Disposition":"attachment; filename=reactions.csv"})
    except Exception as e:
        return JSONResponse({"error":str(e)},500)

@app.get("/api/yield_dist")
def api_yield_dist():
    try:
        c = _conn()
        rows = c.execute("SELECT yield_percent FROM reactions WHERE yield_percent IS NOT NULL").fetchall()
        c.close()
        buckets = [0]*10  # 0-10,10-20,...,90-100
        for (y,) in rows:
            if 0 <= y <= 100:
                buckets[min(int(y//10),9)] += 1
        return {"labels":["0-10","10-20","20-30","30-40","40-50","50-60","60-70","70-80","80-90","90-100"],"values":buckets}
    except Exception as e:
        return JSONResponse({"error":str(e)},500)

# ── Frontend HTML ─────────────────────────────────────────────────────────────
HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>SynAgent · Reaction Database</title>
<script src="https://cdn.tailwindcss.com"></script>
<style>
  body{background:#0d1117;color:#e2e8f0;font-family:system-ui,sans-serif;overflow-x:hidden}
  ::-webkit-scrollbar{width:5px;height:5px}
  ::-webkit-scrollbar-track{background:#161b27}
  ::-webkit-scrollbar-thumb{background:#30374e;border-radius:3px}
  .card{background:#161b27;border:1px solid #21293d}
  .badge-ord{background:#052e16;color:#4ade80;border:1px solid #166534}
  .badge-pat{background:#1e1035;color:#c084fc;border:1px solid #6b21a8}
  .chip{background:#0d1117;border:1px solid #21293d;font-family:monospace;font-size:.7rem;
    padding:1px 7px;border-radius:4px;cursor:pointer;display:inline-block;
    max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;
    transition:border-color .15s,color .15s;vertical-align:middle}
  .chip:hover{border-color:#6366f1;color:#a5b4fc}
  input,select{background:#0d1117;border:1px solid #21293d;color:#e2e8f0;
    border-radius:.375rem;padding:.35rem .65rem;font-size:.82rem}
  input:focus,select:focus{outline:none;border-color:#6366f1}
  input[type=range]{accent-color:#6366f1;width:100%}
  .tab{padding:.45rem 1.1rem;border-radius:.5rem;cursor:pointer;font-size:.85rem;font-weight:600;transition:all .15s}
  .tab.active{background:#1e2a4a;color:#7dd3fc}
  .tab:not(.active){color:#64748b}
  .tab:not(.active):hover{color:#94a3b8}
  .panel{display:none}.panel.active{display:block}
  .stat-big{font-size:2.6rem;font-weight:800;line-height:1}
  .modal-bg{position:fixed;inset:0;background:rgba(0,0,0,.75);z-index:50;display:none;align-items:center;justify-content:center}
  .modal-bg.open{display:flex}
  .modal-box{background:#161b27;border:1px solid #30374e;border-radius:1rem;
    max-width:860px;width:95%;max-height:90vh;overflow-y:auto;padding:2rem}
  .trow:hover{background:#1a2035}
  .copy-toast{position:fixed;bottom:1.5rem;right:1.5rem;background:#1e293b;
    border:1px solid #334155;padding:.5rem 1.1rem;border-radius:.5rem;
    font-size:.8rem;color:#94a3b8;z-index:999;display:none;transition:opacity .2s}
  .bar-fill{background:#0d9488;height:100%;border-radius:2px;transition:width .3s}
  #yield-chart{display:flex;align-items:flex-end;gap:3px;height:80px}
  .yield-bar-wrap{flex:1;display:flex;flex-direction:column;align-items:center;gap:2px}
  .yield-bar{width:100%;background:#0d9488;border-radius:2px 2px 0 0;min-height:2px;transition:height .4s}
  .yield-label{font-size:.55rem;color:#475569;text-align:center;line-height:1.2}
</style>
</head>
<body class="min-h-screen">

<!-- Header -->
<header class="border-b border-[#21293d] px-5 py-3 flex items-center justify-between sticky top-0 z-20"
  style="background:rgba(13,17,23,.95);backdrop-filter:blur(10px)">
  <div class="flex items-center gap-3">
    <span class="text-2xl">⚗️</span>
    <div>
      <h1 class="font-bold text-white text-base leading-none">SynAgent</h1>
      <p class="text-xs text-slate-500 mt-0.5">Reaction Database Explorer</p>
    </div>
  </div>
  <div class="flex items-center gap-3">
    <span id="db-path" class="text-xs text-slate-600 hidden lg:block"></span>
    <a href="/api/export.csv" class="text-xs px-3 py-1.5 rounded-lg border border-[#21293d] text-slate-400 hover:text-white hover:border-teal-600 transition-colors">⬇ Export CSV</a>
  </div>
</header>

<!-- Tabs -->
<div class="border-b border-[#21293d] px-5 flex gap-1 pt-2" style="background:#0d1117">
  <button class="tab active" onclick="switchTab('search')">🔍 Search</button>
  <button class="tab" onclick="switchTab('browse')">📋 Browse All</button>
  <button class="tab" onclick="switchTab('stats')">📊 Dashboard</button>
</div>

<!-- ═══════ SEARCH PANEL ═══════ -->
<div id="panel-search" class="panel active p-5">
  <!-- Search bar -->
  <div class="relative mb-4">
    <span class="absolute left-4 top-1/2 -translate-y-1/2 text-slate-500">🔍</span>
    <input id="s-q" type="text" placeholder="Search keyword, SMILES, SMARTS, solvent, catalyst, reaction type..."
      class="w-full pl-11 pr-28 py-3 rounded-xl border border-[#21293d] focus:border-indigo-500 text-sm"
      style="background:#161b27;font-size:.92rem"
      onkeydown="if(event.key==='Enter')doSearch(1)">
    <button onclick="doSearch(1)" class="absolute right-2 top-1/2 -translate-y-1/2 px-4 py-1.5 rounded-lg text-sm font-semibold text-white" style="background:#4f46e5">Search</button>
  </div>

  <div class="flex gap-4">
    <!-- Filters sidebar -->
    <aside class="w-52 shrink-0 space-y-4">
      <div class="card rounded-xl p-3 space-y-4">
        <div>
          <p class="text-xs font-bold uppercase tracking-widest text-slate-500 mb-2">Source</p>
          <div class="space-y-1.5 text-sm">
            <label class="flex items-center gap-2 cursor-pointer text-slate-300"><input type="radio" name="src" value="all" checked class="accent-indigo-500"> All</label>
            <label class="flex items-center gap-2 cursor-pointer" style="color:#4ade80"><input type="radio" name="src" value="ord" class="accent-indigo-500"> ORD (expert)</label>
            <label class="flex items-center gap-2 cursor-pointer" style="color:#c084fc"><input type="radio" name="src" value="patent" class="accent-indigo-500"> Patent (LLM)</label>
          </div>
        </div>
        <div>
          <p class="text-xs font-bold uppercase tracking-widest text-slate-500 mb-1.5">Yield (%)</p>
          <div class="flex gap-2">
            <input id="s-miny" type="number" placeholder="Min" min="0" max="100" style="width:50%">
            <input id="s-maxy" type="number" placeholder="Max" min="0" max="100" style="width:50%">
          </div>
        </div>
        <div>
          <p class="text-xs font-bold uppercase tracking-widest text-slate-500 mb-1">Confidence ≥ <span id="conf-val" class="text-indigo-400 normal-case">0.0</span></p>
          <input type="range" id="s-conf" min="0" max="1" step="0.05" value="0">
        </div>
        <div>
          <p class="text-xs font-bold uppercase tracking-widest text-slate-500 mb-1.5">Solvent</p>
          <input id="s-solvent" type="text" placeholder="e.g. water">
        </div>
        <div>
          <p class="text-xs font-bold uppercase tracking-widest text-slate-500 mb-1.5">Catalyst</p>
          <input id="s-catalyst" type="text" placeholder="e.g. palladium">
        </div>
        <div>
          <p class="text-xs font-bold uppercase tracking-widest text-slate-500 mb-1.5">Sort by</p>
          <select id="s-sort">
            <option value="confidence">Confidence ↓</option>
            <option value="yield">Yield ↓</option>
            <option value="temperature">Temperature ↑</option>
          </select>
        </div>
        <button onclick="doSearch(1)" class="w-full py-2 rounded-lg text-sm font-semibold text-white" style="background:#4f46e5">Apply</button>
        <button onclick="resetSearch()" class="w-full py-1.5 rounded-lg text-xs text-slate-400 hover:text-white border border-[#21293d] transition-colors">Reset</button>
      </div>
    </aside>

    <!-- Results -->
    <div class="flex-1 min-w-0">
      <div class="flex items-center justify-between mb-3">
        <span id="s-count" class="text-sm text-slate-400"></span>
        <div id="s-pages-top" class="flex gap-2"></div>
      </div>
      <div id="s-results" class="space-y-2.5"></div>
      <div id="s-pages-bot" class="flex justify-center gap-2 mt-5"></div>
    </div>
  </div>
</div>

<!-- ═══════ BROWSE PANEL ═══════ -->
<div id="panel-browse" class="panel p-5">
  <div class="flex items-center gap-3 mb-4 flex-wrap">
    <input id="b-q" type="text" placeholder="Quick filter..." class="w-64 text-sm" onkeydown="if(event.key==='Enter')doBrowse(1)">
    <select id="b-src" class="text-sm">
      <option value="all">All sources</option>
      <option value="ord">ORD only</option>
      <option value="patent">Patent only</option>
    </select>
    <select id="b-sort" class="text-sm">
      <option value="confidence">Sort: Confidence</option>
      <option value="yield">Sort: Yield</option>
      <option value="id">Sort: ID</option>
    </select>
    <select id="b-ps" class="text-sm">
      <option value="25">25/page</option>
      <option value="50">50/page</option>
      <option value="100">100/page</option>
    </select>
    <button onclick="doBrowse(1)" class="px-3 py-1.5 rounded-lg text-sm text-white" style="background:#4f46e5">Filter</button>
    <span id="b-count" class="text-sm text-slate-400 ml-auto"></span>
    <div id="b-pages-top" class="flex gap-2"></div>
  </div>

  <div class="card rounded-xl overflow-hidden">
    <table class="w-full text-xs" style="border-collapse:collapse">
      <thead style="background:#1a2035">
        <tr>
          <th class="px-3 py-2.5 text-left text-slate-400 font-semibold w-8">#</th>
          <th class="px-3 py-2.5 text-left text-slate-400 font-semibold">Source</th>
          <th class="px-3 py-2.5 text-left text-slate-400 font-semibold">Conf</th>
          <th class="px-3 py-2.5 text-left text-slate-400 font-semibold w-32">Product SMILES</th>
          <th class="px-3 py-2.5 text-left text-slate-400 font-semibold">Yield</th>
          <th class="px-3 py-2.5 text-left text-slate-400 font-semibold">Temp</th>
          <th class="px-3 py-2.5 text-left text-slate-400 font-semibold w-36">Solvent</th>
          <th class="px-3 py-2.5 text-left text-slate-400 font-semibold w-36">Catalyst</th>
          <th class="px-3 py-2.5 text-left text-slate-400 font-semibold">SMARTS (preview)</th>
        </tr>
      </thead>
      <tbody id="b-tbody"></tbody>
    </table>
  </div>
  <div id="b-pages-bot" class="flex justify-center gap-2 mt-4"></div>
</div>

<!-- ═══════ STATS PANEL ═══════ -->
<div id="panel-stats" class="panel p-5">
  <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
    <div class="card rounded-xl p-5 text-center">
      <p class="stat-big text-white" id="d-total">—</p>
      <p class="text-xs text-slate-500 mt-1 font-semibold uppercase tracking-widest">Total Reactions</p>
    </div>
    <div class="card rounded-xl p-5 text-center">
      <p class="stat-big" style="color:#4ade80" id="d-ord">—</p>
      <p class="text-xs text-slate-500 mt-1 font-semibold uppercase tracking-widest">ORD Expert</p>
    </div>
    <div class="card rounded-xl p-5 text-center">
      <p class="stat-big" style="color:#c084fc" id="d-pat">—</p>
      <p class="text-xs text-slate-500 mt-1 font-semibold uppercase tracking-widest">Patent LLM</p>
    </div>
    <div class="card rounded-xl p-5 text-center">
      <p class="stat-big text-teal-400" id="d-yld">—</p>
      <p class="text-xs text-slate-500 mt-1 font-semibold uppercase tracking-widest">With Yield Data</p>
    </div>
  </div>

  <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
    <div class="card rounded-xl p-5">
      <h3 class="font-bold text-white mb-3 text-sm">Yield Distribution</h3>
      <div id="yield-chart"></div>
    </div>
    <div class="card rounded-xl p-5">
      <h3 class="font-bold text-white mb-3 text-sm">Database Details</h3>
      <div id="d-details" class="space-y-2.5"></div>
    </div>
  </div>

  <div class="card rounded-xl p-5 mt-4">
    <h3 class="font-bold text-white mb-3 text-sm">Source Breakdown</h3>
    <div id="source-bars" class="space-y-3"></div>
  </div>
</div>

<!-- Reaction Detail Modal -->
<div id="modal" class="modal-bg" onclick="if(event.target===this)closeModal()">
  <div class="modal-box">
    <div class="flex justify-between items-start mb-4">
      <div>
        <h2 class="text-lg font-bold text-white" id="m-title">Reaction Detail</h2>
        <p class="text-xs text-slate-500 mt-0.5" id="m-id"></p>
      </div>
      <button onclick="closeModal()" class="text-slate-400 hover:text-white text-xl px-2">✕</button>
    </div>
    <div id="m-content"></div>
  </div>
</div>

<div id="copy-toast" class="copy-toast">✓ Copied to clipboard</div>

<script>
let sPage=1, bPage=1;

// ── Tab switching ─────────────────────────────────────────────────────────
function switchTab(name) {
  document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
  document.querySelectorAll('.panel').forEach(p=>p.classList.remove('active'));
  event.target.classList.add('active');
  document.getElementById('panel-'+name).classList.add('active');
  if(name==='stats') loadStats();
  if(name==='browse') doBrowse(1);
}

// ── Slider ───────────────────────────────────────────────────────────────
document.getElementById('s-conf').addEventListener('input',function(){
  document.getElementById('conf-val').textContent=parseFloat(this.value).toFixed(2);
});

// ── Search ───────────────────────────────────────────────────────────────
async function doSearch(page) {
  sPage=page||1;
  const params=new URLSearchParams({
    q:document.getElementById('s-q').value.trim(),
    source:document.querySelector('input[name=src]:checked').value,
    min_confidence:document.getElementById('s-conf').value,
    sort:document.getElementById('s-sort').value,
    page:sPage, page_size:25
  });
  const miny=document.getElementById('s-miny').value;
  const maxy=document.getElementById('s-maxy').value;
  const sol=document.getElementById('s-solvent').value.trim();
  const cat=document.getElementById('s-catalyst').value.trim();
  if(miny) params.set('min_yield',miny);
  if(maxy) params.set('max_yield',maxy);
  if(sol) params.set('solvent',sol);
  if(cat) params.set('catalyst',cat);

  document.getElementById('s-results').innerHTML='<p class="text-slate-500 text-sm text-center py-10">Searching...</p>';
  const res=await fetch('/api/search?'+params);
  const data=await res.json();
  if(data.error){document.getElementById('s-results').innerHTML=`<div class="card rounded-xl p-5 text-red-400 text-sm">Error: ${data.error}</div>`;return;}
  document.getElementById('s-count').textContent=`${data.total.toLocaleString()} result${data.total!==1?'s':''}`;
  renderCards(data.results,'s-results');
  renderPages(data.page,data.total_pages,'s-pages-top','s-pages-bot',doSearch);
}

function renderCards(results,containerId) {
  const el=document.getElementById(containerId);
  if(!results.length){el.innerHTML='<p class="text-slate-500 text-sm text-center py-14">No reactions found.</p>';return;}
  el.innerHTML=results.map((r,i)=>{
    const isOrd=r.source==='ord';
    const badge=isOrd?'<span class="badge-ord text-xs px-2 py-0.5 rounded-full font-semibold">ORD · Expert</span>'
                     :`<span class="badge-pat text-xs px-2 py-0.5 rounded-full font-semibold">Patent · LLM</span>`;
    const conf=typeof r.confidence==='number'?`<span class="text-xs ${r.confidence>=0.7?'text-green-400':r.confidence>=0.4?'text-yellow-400':'text-red-400'}">${(r.confidence*100).toFixed(0)}%</span>`:'';
    const yieldStr=r.yield_percent!=null?`<span class="text-white font-semibold text-sm">${r.yield_percent.toFixed(1)}%</span><span class="text-slate-500 text-xs ml-1">yield</span>`:'';
    const tempStr=r.temperature_celsius!=null?`<span class="text-white text-sm ml-3">${r.temperature_celsius.toFixed(0)}°C</span>`:'';
    const smarts=r.reaction_smarts?`<div class="mt-2"><span class="text-slate-500 text-xs">SMARTS · </span><span class="chip" onclick="copy('${esc(r.reaction_smarts)}')" title="${esc(r.reaction_smarts)}">${r.reaction_smarts.slice(0,110)}${r.reaction_smarts.length>110?'…':''}</span></div>`:'';
    const prod=r.product_smiles?`<div class="mt-1.5"><span class="text-slate-500 text-xs">Product · </span><span class="chip" onclick="copy('${esc(r.product_smiles)}')">${r.product_smiles.slice(0,90)}${r.product_smiles.length>90?'…':''}</span></div>`:'';
    const sol=r.solvent?`<div class="mt-1.5"><span class="text-slate-500 text-xs">Solvent · </span><span class="chip" onclick="copy('${esc(r.solvent)}')">${r.solvent.slice(0,60)}${r.solvent.length>60?'…':''}</span></div>`:'';
    const cat=r.catalyst?`<div class="mt-1.5"><span class="text-slate-500 text-xs">Catalyst · </span><span class="chip" onclick="copy('${esc(r.catalyst)}')">${r.catalyst.slice(0,80)}${r.catalyst.length>80?'…':''}</span></div>`:'';
    const notes=r.notes?`<p class="text-slate-400 text-xs mt-2 leading-relaxed">${r.notes.slice(0,260)}${r.notes.length>260?'…':''}</p>`:'';
    return `<div class="card rounded-xl p-4 hover:border-indigo-800 transition-colors cursor-pointer" onclick="openDetail('${esc(r.reaction_id)}')">
      <div class="flex items-center justify-between flex-wrap gap-2 mb-2">
        <div class="flex items-center gap-2 flex-wrap">${badge}${conf}<span class="text-slate-600 text-xs font-mono">${(r.reaction_id||'').slice(0,24)}</span></div>
        <div class="flex items-center">${yieldStr}${tempStr}</div>
      </div>
      ${prod}${smarts}${sol}${cat}${notes}
    </div>`;
  }).join('');
}

function renderPages(page,total,topId,botId,fn) {
  if(total<=1){[topId,botId].forEach(id=>document.getElementById(id).innerHTML='');return;}
  const html=`<button onclick="${fn.name}(${page-1})" ${page<=1?'disabled':''} class="px-3 py-1.5 rounded-lg text-xs border border-[#21293d] text-slate-400 hover:text-white disabled:opacity-30 disabled:cursor-not-allowed">← Prev</button>
    <span class="px-2 py-1.5 text-xs text-slate-400">${page} / ${total}</span>
    <button onclick="${fn.name}(${page+1})" ${page>=total?'disabled':''} class="px-3 py-1.5 rounded-lg text-xs border border-[#21293d] text-slate-400 hover:text-white disabled:opacity-30 disabled:cursor-not-allowed">Next →</button>`;
  document.getElementById(topId).innerHTML=html;
  document.getElementById(botId).innerHTML=html;
}

// ── Browse ────────────────────────────────────────────────────────────────
async function doBrowse(page) {
  bPage=page||1;
  const ps=parseInt(document.getElementById('b-ps').value)||25;
  const params=new URLSearchParams({
    q:document.getElementById('b-q').value.trim(),
    source:document.getElementById('b-src').value,
    sort:document.getElementById('b-sort').value,
    page:bPage, page_size:ps
  });
  const res=await fetch('/api/search?'+params);
  const data=await res.json();
  if(data.error) return;
  document.getElementById('b-count').textContent=`${data.total.toLocaleString()} reactions`;
  const tbody=document.getElementById('b-tbody');
  const offset=(bPage-1)*ps;
  tbody.innerHTML=data.results.map((r,i)=>{
    const isOrd=r.source==='ord';
    const badge=isOrd?'<span class="badge-ord px-1.5 py-0.5 rounded text-xs font-semibold">ORD</span>'
                     :'<span class="badge-pat px-1.5 py-0.5 rounded text-xs font-semibold">Patent</span>';
    const conf=typeof r.confidence==='number'?`<span class="${r.confidence>=0.7?'text-green-400':r.confidence>=0.4?'text-yellow-400':'text-red-400'}">${(r.confidence*100).toFixed(0)}%</span>`:'-';
    const tr=r=>r?`<span class="chip" onclick="event.stopPropagation();copy('${esc(r)}')">${r.slice(0,28)}${r.length>28?'…':''}</span>`:'-';
    return `<tr class="trow border-t border-[#21293d] cursor-pointer" onclick="openDetail('${esc(r.reaction_id)}')">
      <td class="px-3 py-2 text-slate-500">${offset+i+1}</td>
      <td class="px-3 py-2">${badge}</td>
      <td class="px-3 py-2">${conf}</td>
      <td class="px-3 py-2">${tr(r.product_smiles)}</td>
      <td class="px-3 py-2 text-white">${r.yield_percent!=null?r.yield_percent.toFixed(1)+'%':'-'}</td>
      <td class="px-3 py-2 text-slate-300">${r.temperature_celsius!=null?r.temperature_celsius.toFixed(0)+'°C':'-'}</td>
      <td class="px-3 py-2">${tr(r.solvent)}</td>
      <td class="px-3 py-2">${tr(r.catalyst)}</td>
      <td class="px-3 py-2 font-mono text-indigo-400">${r.reaction_smarts?(r.reaction_smarts.slice(0,40)+(r.reaction_smarts.length>40?'…':'')):'-'}</td>
    </tr>`;
  }).join('');
  renderPages(data.page,data.total_pages,'b-pages-top','b-pages-bot',doBrowse);
}

// ── Detail modal ──────────────────────────────────────────────────────────
async function openDetail(rid) {
  const res=await fetch('/api/reaction/'+encodeURIComponent(rid));
  const r=await res.json();
  if(r.error) return;
  document.getElementById('m-title').textContent=r.reaction_id||'Reaction';
  document.getElementById('m-id').textContent=(r.source==='ord'?'ORD Expert-Curated':'LLM-Extracted from Patent')+' · confidence: '+(r.confidence!=null?(r.confidence*100).toFixed(0)+'%':'—');
  const field=(label,val,mono=false)=>val?`<div class="mb-3"><p class="text-xs text-slate-500 uppercase tracking-widest font-semibold mb-1">${label}</p><p class="${mono?'font-mono text-indigo-300 text-xs bg-[#0d1117] p-2 rounded-lg':'text-sm text-slate-200'} break-all">${val}</p></div>`:'';
  const clickField=(label,val)=>val?`<div class="mb-3"><p class="text-xs text-slate-500 uppercase tracking-widest font-semibold mb-1">${label}</p><span class="chip text-xs" onclick="copy('${esc(val)}')" title="${esc(val)}">${val}</span></div>`:'';
  const numField=(label,val,unit='')=>val!=null?`<div class="mb-3"><p class="text-xs text-slate-500 uppercase tracking-widest font-semibold mb-1">${label}</p><p class="text-xl font-bold text-white">${val}${unit}</p></div>`:'';
  const reactants=(r.reactants||[]).map(s=>`<span class="chip text-xs mr-1 mb-1" onclick="copy('${esc(s)}')">${s}</span>`).join('');
  document.getElementById('m-content').innerHTML=`
    <div class="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
      ${numField('Yield',r.yield_percent!=null?r.yield_percent.toFixed(1):'—','%')}
      ${numField('Temp',r.temperature_celsius!=null?r.temperature_celsius.toFixed(0):'—','°C')}
      ${numField('Time',r.time_hours!=null?r.time_hours.toFixed(1):'—','h')}
      ${numField('Confidence',r.confidence!=null?(r.confidence*100).toFixed(0):'—','%')}
    </div>
    ${field('Reaction SMARTS',r.reaction_smarts,true)}
    ${clickField('Product SMILES',r.product_smiles)}
    ${reactants?`<div class="mb-3"><p class="text-xs text-slate-500 uppercase tracking-widest font-semibold mb-1">Reactants</p><div>${reactants}</div></div>`:''}
    ${clickField('Solvent',r.solvent)}
    ${clickField('Catalyst',r.catalyst)}
    ${field('Mechanism',r.mechanism_text)}
    ${field('Notes',r.notes)}
    ${r.patent_id?`<div class="mt-3 pt-3 border-t border-[#21293d]"><p class="text-xs text-slate-600">Source: ${r.patent_id}</p></div>`:''}
  `;
  document.getElementById('modal').classList.add('open');
}
function closeModal(){document.getElementById('modal').classList.remove('open');}
document.addEventListener('keydown',e=>{if(e.key==='Escape')closeModal();});

// ── Stats ─────────────────────────────────────────────────────────────────
async function loadStats() {
  const [s,y]=await Promise.all([fetch('/api/stats').then(r=>r.json()),fetch('/api/yield_dist').then(r=>r.json())]);
  if(s.error) return;
  document.getElementById('d-total').textContent=s.total.toLocaleString();
  document.getElementById('d-ord').textContent=s.ord.toLocaleString();
  document.getElementById('d-pat').textContent=s.patent.toLocaleString();
  document.getElementById('d-yld').textContent=s.with_yield.toLocaleString();
  if(s.db_path) document.getElementById('db-path').textContent=s.db_path;

  document.getElementById('d-details').innerHTML=[
    ['High Confidence (≥ 0.6)',s.high_confidence?.toLocaleString()],
    ['Avg Yield',s.avg_yield!=null?s.avg_yield+'%':'—'],
    ['Distinct Solvents',s.distinct_solvents?.toLocaleString()],
    ['Distinct Catalysts',s.distinct_catalysts?.toLocaleString()],
    ['Database Path',s.db_path],
  ].map(([k,v])=>`<div class="flex justify-between text-sm"><span class="text-slate-500">${k}</span><span class="text-white font-semibold">${v}</span></div>`).join('');

  const total=s.total||1;
  document.getElementById('source-bars').innerHTML=[
    ['ORD Expert-Curated',s.ord,'4ade80'],
    ['Patent LLM-Extracted',s.patent,'c084fc'],
    ['High Confidence',s.high_confidence,'38bdf8'],
    ['With Yield Data',s.with_yield,'fb923c'],
  ].map(([label,val,color])=>`<div><div class="flex justify-between text-xs text-slate-400 mb-1"><span>${label}</span><span style="color:#${color}">${val?.toLocaleString()||0}</span></div>
    <div class="h-2 rounded-full" style="background:#1e2a3a"><div class="h-full rounded-full" style="background:#${color};width:${Math.round((val/total)*100)}%"></div></div></div>`).join('');

  if(y&&y.values){
    const max=Math.max(...y.values,1);
    document.getElementById('yield-chart').innerHTML=y.values.map((v,i)=>`
      <div class="yield-bar-wrap">
        <div class="yield-bar" style="height:${Math.round((v/max)*76)+4}px" title="${y.labels[i]}: ${v} reactions"></div>
        <p class="yield-label">${y.labels[i].split('-')[0]}</p>
      </div>`).join('');
  }
}

// ── Utilities ─────────────────────────────────────────────────────────────
function copy(text) {
  navigator.clipboard.writeText(text).then(()=>{
    const t=document.getElementById('copy-toast');
    t.style.display='block';
    setTimeout(()=>t.style.display='none',1800);
  });
}
function esc(s){if(!s)return'';return String(s).replace(/\\/g,'\\\\').replace(/'/g,"\\'").replace(/"/g,'&quot;');}
function resetSearch(){
  document.getElementById('s-q').value='';
  document.querySelector('input[name=src][value=all]').checked=true;
  document.getElementById('s-miny').value='';document.getElementById('s-maxy').value='';
  document.getElementById('s-conf').value=0;document.getElementById('conf-val').textContent='0.0';
  document.getElementById('s-solvent').value='';document.getElementById('s-catalyst').value='';
  document.getElementById('s-sort').value='confidence';
  doSearch(1);
}

// ── Init ──────────────────────────────────────────────────────────────────
fetch('/api/stats').then(r=>r.json()).then(s=>{if(s.db_path)document.getElementById('db-path').textContent=s.db_path;});
doSearch(1);
</script>
</body>
</html>"""

@app.get("/", response_class=HTMLResponse)
def index():
    return HTML

# ── Tkinter launcher window ──────────────────────────────────────────────────
def launch_ui():
    """Minimal status window — keeps process alive while server runs."""
    try:
        import tkinter as tk
        from tkinter import ttk

        root = tk.Tk()
        root.title("SynAgent Reaction Database")
        root.geometry("380x160")
        root.configure(bg="#0d1117")
        root.resizable(False, False)

        db = _find_db()

        # Title
        tk.Label(root, text="⚗️  SynAgent Reaction DB", bg="#0d1117",
                 fg="white", font=("Segoe UI", 13, "bold")).pack(pady=(18,2))

        # Status
        status_text = f"Running at http://localhost:8001" if db else "⚠ Database not found"
        status_color = "#4ade80" if db else "#f87171"
        tk.Label(root, text=status_text, bg="#0d1117",
                 fg=status_color, font=("Segoe UI", 9)).pack()

        if db:
            tk.Label(root, text=str(db), bg="#0d1117",
                     fg="#475569", font=("Segoe UI", 8)).pack(pady=(1,0))

        # Buttons
        btn_frame = tk.Frame(root, bg="#0d1117")
        btn_frame.pack(pady=16)

        def open_browser():
            webbrowser.open("http://localhost:8001")

        tk.Button(btn_frame, text="Open in Browser", command=open_browser,
                  bg="#4f46e5", fg="white", font=("Segoe UI", 9, "bold"),
                  relief="flat", padx=14, pady=6, cursor="hand2").pack(side="left", padx=5)

        tk.Button(btn_frame, text="Close App", command=root.destroy,
                  bg="#1e293b", fg="#94a3b8", font=("Segoe UI", 9),
                  relief="flat", padx=14, pady=6, cursor="hand2").pack(side="left", padx=5)

        root.mainloop()
    except Exception:
        # No display — just block
        try:
            input("Server running at http://localhost:8001 — press Enter to stop...")
        except Exception:
            threading.Event().wait()

# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn

    db = _find_db()
    if db:
        print(f"[OK] Database: {db}")
    else:
        print("[WARNING] Database not found — looking for db/patent_pipeline.db next to this file")

    print("[*] Starting server at http://localhost:8001 ...")

    # Start FastAPI in background thread
    def run_server():
        uvicorn.run(app, host="127.0.0.1", port=8001, log_level="error")

    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()

    # Wait briefly then open browser
    time.sleep(1.5)
    webbrowser.open("http://127.0.0.1:8001")

    # Show status window (blocks until user closes it)
    launch_ui()
