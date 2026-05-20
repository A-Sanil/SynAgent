"""
SynAgent Reaction Database — True Standalone Desktop App
=========================================================
No server. No ports. No internet. No dependencies beyond the EXE itself.
Uses a custom Qt URL scheme so Python handles every data request directly.
Works on Windows (.exe) and macOS (.app).

Double-click the binary. The DB is found automatically next to it.
"""
from __future__ import annotations

import io
import json
import os
import signal
import sqlite3
import sys
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse, unquote

# ── Embedded Tailwind (offline) ───────────────────────────────────────────────
try:
    from _tailwind_data import TAILWIND_JS as _TW
    TAILWIND_JS: bytes = _TW
except ImportError:
    TAILWIND_JS = b""  # graceful fallback

# ── CRITICAL: register custom URL scheme BEFORE QApplication ─────────────────
# This MUST run before any Qt object is created.
from PySide6.QtWebEngineCore import QWebEngineUrlScheme
_scheme = QWebEngineUrlScheme(b"app")
_scheme.setFlags(
    QWebEngineUrlScheme.Flag.SecureScheme
    | QWebEngineUrlScheme.Flag.LocalScheme
    | QWebEngineUrlScheme.Flag.LocalAccessAllowed
    | QWebEngineUrlScheme.Flag.CorsEnabled
    | QWebEngineUrlScheme.Flag.FetchApiAllowed
)
QWebEngineUrlScheme.registerScheme(_scheme)

# ── DB discovery ──────────────────────────────────────────────────────────────
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).parent
else:
    BASE_DIR = Path(__file__).parent.parent.parent

def _find_db() -> Optional[Path]:
    candidates = [
        os.environ.get("PATENT_DATA_DIR"),
        str(BASE_DIR),
        r"D:\SynAgent", r"E:\SynAgent", r"F:\SynAgent", r"G:\SynAgent",
        str(Path(__file__).parent.parent / "patent_ingestion_pipeline" / "data"),
    ]
    for c in candidates:
        if not c:
            continue
        p = Path(c) / "db" / "patent_pipeline.db"
        if p.exists():
            return p
    return None

# ── Pure-Python DB query functions (no FastAPI needed) ────────────────────────

def db_stats() -> dict:
    db = _find_db()
    if not db:
        return {"error": "Database not found"}
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        total    = conn.execute("SELECT COUNT(*) FROM reactions").fetchone()[0]
        ord_c    = conn.execute("SELECT COUNT(*) FROM reactions WHERE source='ord'").fetchone()[0]
        patent_c = conn.execute("SELECT COUNT(*) FROM reactions WHERE source='patent'").fetchone()[0]
        hi       = conn.execute("SELECT COUNT(*) FROM reactions WHERE confidence>=0.6").fetchone()[0]
        yld      = conn.execute("SELECT COUNT(*) FROM reactions WHERE yield_percent IS NOT NULL").fetchone()[0]
        avg_r    = conn.execute("SELECT AVG(yield_percent) FROM reactions WHERE yield_percent IS NOT NULL").fetchone()[0]
        solvents = conn.execute("SELECT COUNT(DISTINCT solvent) FROM reactions WHERE solvent IS NOT NULL AND solvent!=''").fetchone()[0]
        cats     = conn.execute("SELECT COUNT(DISTINCT catalyst) FROM reactions WHERE catalyst IS NOT NULL AND catalyst!=''").fetchone()[0]
        return dict(total=total, ord=ord_c, patent=patent_c,
                    high_confidence=hi, with_yield=yld,
                    avg_yield=round(avg_r, 1) if avg_r else None,
                    distinct_solvents=solvents, distinct_catalysts=cats,
                    db_path=str(db))
    finally:
        conn.close()

def db_search(q="", source="all", min_yield=None, max_yield=None,
              min_confidence=0.0, solvent="", catalyst="",
              sort="confidence", page=1, page_size=25) -> dict:
    db = _find_db()
    if not db:
        return {"error": "Database not found"}
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        params: list = []
        conds: list[str] = []

        if q.strip():
            try:
                fts = conn.execute(
                    "SELECT reaction_id FROM reactions_fts WHERE reactions_fts MATCH ? LIMIT 5000",
                    (q.strip(),)
                ).fetchall()
                ids = [r[0] for r in fts]
                if ids:
                    conds.append(f"r.reaction_id IN ({','.join('?'*len(ids))})")
                    params.extend(ids)
                else:
                    like = f"%{q.strip()[:60]}%"
                    conds.append("(r.reaction_smarts LIKE ? OR r.product_smiles LIKE ? OR r.solvent LIKE ? OR r.catalyst LIKE ? OR r.notes LIKE ?)")
                    params.extend([like] * 5)
            except sqlite3.OperationalError:
                like = f"%{q.strip()[:60]}%"
                conds.append("(r.reaction_smarts LIKE ? OR r.product_smiles LIKE ? OR r.solvent LIKE ? OR r.catalyst LIKE ?)")
                params.extend([like] * 4)

        if source in ("ord", "patent"):
            conds.append("r.source=?"); params.append(source)
        if min_yield is not None:
            conds.append("r.yield_percent>=?"); params.append(float(min_yield))
        if max_yield is not None:
            conds.append("r.yield_percent<=?"); params.append(float(max_yield))
        if float(min_confidence) > 0:
            conds.append("r.confidence>=?"); params.append(float(min_confidence))
        if solvent.strip():
            conds.append("r.solvent LIKE ?"); params.append(f"%{solvent.strip()}%")
        if catalyst.strip():
            conds.append("r.catalyst LIKE ?"); params.append(f"%{catalyst.strip()}%")

        where = ("WHERE " + " AND ".join(conds)) if conds else ""
        sort_map = {
            "confidence": "r.confidence DESC",
            "yield": "r.yield_percent DESC NULLS LAST",
            "temperature": "r.temperature_celsius ASC NULLS LAST",
            "id": "r.rowid ASC",
        }
        order = sort_map.get(sort, "r.confidence DESC")

        total = conn.execute(f"SELECT COUNT(*) FROM reactions r {where}", params).fetchone()[0]
        page = max(1, int(page))
        page_size = max(1, min(200, int(page_size)))
        offset = (page - 1) * page_size

        rows = conn.execute(
            f"SELECT r.* FROM reactions r {where} ORDER BY {order} LIMIT ? OFFSET ?",
            params + [page_size, offset]
        ).fetchall()

        results = []
        for row in rows:
            d = dict(row)
            try:
                d["reactants"] = json.loads(d.get("reactant_smiles_json") or "[]")
            except Exception:
                d["reactants"] = []
            results.append(d)

        return dict(total=total, page=page, page_size=page_size,
                    total_pages=max(1, (total + page_size - 1) // page_size),
                    results=results)
    finally:
        conn.close()

def db_reaction(rid: str) -> dict:
    db = _find_db()
    if not db:
        return {"error": "Database not found"}
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM reactions WHERE reaction_id=?", (rid,)).fetchone()
        if not row:
            return {"error": "Not found"}
        d = dict(row)
        try:
            d["reactants"] = json.loads(d.get("reactant_smiles_json") or "[]")
        except Exception:
            d["reactants"] = []
        return d
    finally:
        conn.close()

def db_yield_dist() -> dict:
    db = _find_db()
    if not db:
        return {"labels": [], "values": []}
    conn = sqlite3.connect(str(db))
    try:
        rows = conn.execute("SELECT yield_percent FROM reactions WHERE yield_percent IS NOT NULL").fetchall()
        buckets = [0] * 10
        for (y,) in rows:
            if 0 <= y <= 100:
                buckets[min(int(y // 10), 9)] += 1
        return {"labels": ["0-10","10-20","20-30","30-40","40-50","50-60","60-70","70-80","80-90","90-100"],
                "values": buckets}
    finally:
        conn.close()

def db_export_csv(q="", source="all", min_yield=None, min_confidence=0.0) -> bytes:
    db = _find_db()
    if not db:
        return b"error,Database not found\n"
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        conds, params = [], []
        if q.strip():
            try:
                fts = conn.execute("SELECT reaction_id FROM reactions_fts WHERE reactions_fts MATCH ? LIMIT 10000", (q.strip(),)).fetchall()
                ids = [r[0] for r in fts]
                if ids:
                    conds.append(f"reaction_id IN ({','.join('?'*len(ids))})"); params.extend(ids)
            except Exception:
                pass
        if source in ("ord", "patent"):
            conds.append("source=?"); params.append(source)
        if min_yield is not None:
            conds.append("yield_percent>=?"); params.append(float(min_yield))
        if float(min_confidence) > 0:
            conds.append("confidence>=?"); params.append(float(min_confidence))
        where = ("WHERE " + " AND ".join(conds)) if conds else ""
        rows = conn.execute(
            f"SELECT reaction_id,source,confidence,reaction_smarts,product_smiles,"
            f"yield_percent,temperature_celsius,solvent,catalyst,notes "
            f"FROM reactions {where} ORDER BY confidence DESC LIMIT 10000", params
        ).fetchall()
        buf = io.StringIO()
        buf.write("reaction_id,source,confidence,reaction_smarts,product_smiles,yield_percent,temperature_celsius,solvent,catalyst,notes\n")
        for row in rows:
            buf.write(",".join(
                f'"{str(v).replace(chr(34), chr(39))}"' if v is not None else ""
                for v in row
            ) + "\n")
        return buf.getvalue().encode("utf-8")
    finally:
        conn.close()

# ── HTML frontend ─────────────────────────────────────────────────────────────
# All fetch() calls use app://localhost/api/... — no HTTP server needed.
HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>SynAgent</title>
<script src="app://localhost/tailwind.js"></script>
<style>
  *{box-sizing:border-box;-webkit-user-select:none;user-select:none}
  input,textarea{-webkit-user-select:text!important;user-select:text!important}
  body{background:#0d1117;color:#e2e8f0;font-family:system-ui,sans-serif;margin:0;overflow-x:hidden}
  ::-webkit-scrollbar{width:5px;height:5px}::-webkit-scrollbar-track{background:#161b27}::-webkit-scrollbar-thumb{background:#30374e;border-radius:3px}
  .card{background:#161b27;border:1px solid #21293d}
  .badge-ord{background:#052e16;color:#4ade80;border:1px solid #166534}
  .badge-pat{background:#1e1035;color:#c084fc;border:1px solid #6b21a8}
  .chip{background:#0d1117;border:1px solid #21293d;font-family:monospace;font-size:.7rem;padding:1px 7px;border-radius:4px;cursor:pointer;display:inline-block;max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;vertical-align:middle;transition:border-color .15s,color .15s;-webkit-user-select:text!important}
  .chip:hover{border-color:#6366f1;color:#a5b4fc}
  input,select{background:#0d1117;border:1px solid #21293d;color:#e2e8f0;border-radius:.375rem;padding:.35rem .65rem;font-size:.82rem}
  input:focus,select:focus{outline:none;border-color:#6366f1}
  input[type=range]{accent-color:#6366f1;width:100%}
  .tab{padding:.45rem 1.1rem;border-radius:.5rem;cursor:pointer;font-size:.85rem;font-weight:600;transition:all .15s;border:none;background:none}
  .tab.active{background:#1e2a4a;color:#7dd3fc}.tab:not(.active){color:#64748b}.tab:not(.active):hover{color:#94a3b8}
  .panel{display:none}.panel.active{display:block}
  .modal-bg{position:fixed;inset:0;background:rgba(0,0,0,.8);z-index:50;display:none;align-items:center;justify-content:center}
  .modal-bg.open{display:flex}
  .modal-box{background:#161b27;border:1px solid #30374e;border-radius:1rem;max-width:860px;width:95%;max-height:90vh;overflow-y:auto;padding:2rem}
  .trow:hover{background:#1a2035;cursor:pointer}
  .toast{position:fixed;bottom:1.5rem;right:1.5rem;background:#1e293b;border:1px solid #334155;padding:.5rem 1.1rem;border-radius:.5rem;font-size:.8rem;color:#94a3b8;z-index:999;display:none}
  .yield-bar{background:#0d9488;border-radius:2px 2px 0 0;min-height:2px;width:100%;transition:height .4s}
  #titlebar{-webkit-app-region:drag;height:38px;background:#080d14;display:flex;align-items:center;padding:0 14px;border-bottom:1px solid #1a2035;position:sticky;top:0;z-index:100;gap:10px}
  #titlebar .nd{-webkit-app-region:no-drag}
</style>
</head>
<body class="min-h-screen flex flex-col">

<!-- Title bar -->
<div id="titlebar">
  <span style="font-size:1.2rem">⚗️</span>
  <span class="font-bold text-white text-sm">SynAgent</span>
  <span class="text-slate-600 text-xs">Reaction Database</span>
  <div class="flex-1"></div>
  <span id="db-badge" class="nd text-xs text-slate-600 hidden lg:block"></span>
  <button onclick="exportCSV()" class="nd text-xs px-3 py-1 rounded border border-[#21293d] text-slate-400 hover:text-white hover:border-teal-700 transition-colors">⬇ Export CSV</button>
  <button onclick="shutdown()" class="nd text-xs px-3 py-1 rounded border border-[#3d1a1a] text-red-800 hover:text-red-400 hover:border-red-700 transition-colors font-semibold">⏻ Quit</button>
</div>

<!-- Tabs -->
<div class="border-b border-[#21293d] px-4 flex gap-1 pt-1.5 shrink-0" style="background:#0d1117">
  <button class="tab active" onclick="switchTab('search',this)">🔍 Search</button>
  <button class="tab" onclick="switchTab('browse',this)">📋 Browse</button>
  <button class="tab" onclick="switchTab('stats',this)">📊 Dashboard</button>
</div>

<!-- SEARCH -->
<div id="panel-search" class="panel active p-4">
  <div class="relative mb-3">
    <span class="absolute left-4 top-1/2 -translate-y-1/2 text-slate-500">🔍</span>
    <input id="s-q" type="text" placeholder="Search keyword, SMILES, SMARTS, solvent, catalyst, reaction type..."
      class="w-full pl-11 pr-28 py-2.5 rounded-xl border border-[#21293d] focus:border-indigo-500"
      style="background:#161b27;font-size:.9rem" onkeydown="if(event.key==='Enter')doSearch(1)">
    <button onclick="doSearch(1)" class="absolute right-2 top-1/2 -translate-y-1/2 px-4 py-1.5 rounded-lg text-sm font-semibold text-white" style="background:#4f46e5">Search</button>
  </div>
  <div class="flex gap-3">
    <aside class="w-48 shrink-0">
      <div class="card rounded-xl p-3 space-y-3 text-sm">
        <div>
          <p class="text-xs font-bold uppercase tracking-widest text-slate-500 mb-1.5">Source</p>
          <div class="space-y-1">
            <label class="flex items-center gap-2 cursor-pointer text-slate-300"><input type="radio" name="src" value="all" checked> All</label>
            <label class="flex items-center gap-2 cursor-pointer" style="color:#4ade80"><input type="radio" name="src" value="ord"> ORD expert</label>
            <label class="flex items-center gap-2 cursor-pointer" style="color:#c084fc"><input type="radio" name="src" value="patent"> Patent LLM</label>
          </div>
        </div>
        <div>
          <p class="text-xs font-bold uppercase tracking-widest text-slate-500 mb-1">Yield (%)</p>
          <div class="flex gap-1.5"><input id="s-miny" type="number" placeholder="Min" min="0" max="100" style="width:50%"><input id="s-maxy" type="number" placeholder="Max" min="0" max="100" style="width:50%"></div>
        </div>
        <div>
          <p class="text-xs font-bold uppercase tracking-widest text-slate-500 mb-1">Conf ≥ <span id="conf-val" class="text-indigo-400 normal-case">0.0</span></p>
          <input type="range" id="s-conf" min="0" max="1" step="0.05" value="0">
        </div>
        <div><p class="text-xs font-bold uppercase tracking-widest text-slate-500 mb-1">Solvent</p><input id="s-solvent" type="text" placeholder="e.g. water" class="w-full"></div>
        <div><p class="text-xs font-bold uppercase tracking-widest text-slate-500 mb-1">Catalyst</p><input id="s-catalyst" type="text" placeholder="e.g. palladium" class="w-full"></div>
        <div>
          <p class="text-xs font-bold uppercase tracking-widest text-slate-500 mb-1">Sort</p>
          <select id="s-sort" class="w-full"><option value="confidence">Confidence</option><option value="yield">Yield</option><option value="temperature">Temp</option></select>
        </div>
        <button onclick="doSearch(1)" class="w-full py-1.5 rounded-lg text-xs font-bold text-white" style="background:#4f46e5">Apply</button>
        <button onclick="resetSearch()" class="w-full py-1 rounded-lg text-xs text-slate-400 hover:text-white border border-[#21293d]">Reset</button>
      </div>
    </aside>
    <div class="flex-1 min-w-0">
      <div class="flex items-center justify-between mb-2"><span id="s-count" class="text-xs text-slate-400"></span><div id="s-pages-top" class="flex gap-1.5"></div></div>
      <div id="s-results" class="space-y-2"></div>
      <div id="s-pages-bot" class="flex justify-center gap-1.5 mt-4"></div>
    </div>
  </div>
</div>

<!-- BROWSE -->
<div id="panel-browse" class="panel p-4">
  <div class="flex items-center gap-2 mb-3 flex-wrap">
    <input id="b-q" type="text" placeholder="Quick filter..." class="w-52 text-sm" onkeydown="if(event.key==='Enter')doBrowse(1)">
    <select id="b-src" class="text-sm"><option value="all">All</option><option value="ord">ORD</option><option value="patent">Patent</option></select>
    <select id="b-sort" class="text-sm"><option value="confidence">Confidence</option><option value="yield">Yield</option><option value="id">Row ID</option></select>
    <select id="b-ps" class="text-sm"><option value="25">25/page</option><option value="50">50/page</option><option value="100">100/page</option></select>
    <button onclick="doBrowse(1)" class="px-3 py-1.5 rounded-lg text-xs text-white font-bold" style="background:#4f46e5">Filter</button>
    <span id="b-count" class="text-xs text-slate-400 ml-auto"></span>
    <div id="b-pages-top" class="flex gap-1.5"></div>
  </div>
  <div class="card rounded-xl overflow-auto" style="max-height:calc(100vh - 195px)">
    <table class="w-full text-xs" style="border-collapse:collapse">
      <thead style="background:#1a2035;position:sticky;top:0">
        <tr>
          <th class="px-3 py-2 text-left text-slate-400 font-semibold">#</th>
          <th class="px-3 py-2 text-left text-slate-400 font-semibold">Source</th>
          <th class="px-3 py-2 text-left text-slate-400 font-semibold">Conf</th>
          <th class="px-3 py-2 text-left text-slate-400 font-semibold">Product</th>
          <th class="px-3 py-2 text-left text-slate-400 font-semibold">Yield</th>
          <th class="px-3 py-2 text-left text-slate-400 font-semibold">Temp</th>
          <th class="px-3 py-2 text-left text-slate-400 font-semibold">Solvent</th>
          <th class="px-3 py-2 text-left text-slate-400 font-semibold">Catalyst</th>
          <th class="px-3 py-2 text-left text-slate-400 font-semibold">SMARTS</th>
        </tr>
      </thead>
      <tbody id="b-tbody"></tbody>
    </table>
  </div>
  <div id="b-pages-bot" class="flex justify-center gap-1.5 mt-3"></div>
</div>

<!-- STATS -->
<div id="panel-stats" class="panel p-4">
  <div class="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
    <div class="card rounded-xl p-4 text-center"><p class="text-3xl font-black text-white" id="d-total">—</p><p class="text-xs text-slate-500 mt-1 uppercase tracking-widest font-semibold">Total</p></div>
    <div class="card rounded-xl p-4 text-center"><p class="text-3xl font-black" style="color:#4ade80" id="d-ord">—</p><p class="text-xs text-slate-500 mt-1 uppercase tracking-widest font-semibold">ORD Expert</p></div>
    <div class="card rounded-xl p-4 text-center"><p class="text-3xl font-black" style="color:#c084fc" id="d-pat">—</p><p class="text-xs text-slate-500 mt-1 uppercase tracking-widest font-semibold">Patent LLM</p></div>
    <div class="card rounded-xl p-4 text-center"><p class="text-3xl font-black text-teal-400" id="d-yld">—</p><p class="text-xs text-slate-500 mt-1 uppercase tracking-widest font-semibold">With Yield</p></div>
  </div>
  <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
    <div class="card rounded-xl p-4">
      <h3 class="font-bold text-white mb-3 text-sm">Yield Distribution</h3>
      <div id="yield-chart" class="flex items-end gap-1" style="height:70px"></div>
      <div class="flex justify-between mt-1"><span class="text-xs text-slate-600">0%</span><span class="text-xs text-slate-600">100%</span></div>
    </div>
    <div class="card rounded-xl p-4">
      <h3 class="font-bold text-white mb-3 text-sm">Details</h3>
      <div id="d-details" class="space-y-2"></div>
    </div>
  </div>
  <div class="card rounded-xl p-4 mt-3">
    <h3 class="font-bold text-white mb-3 text-sm">Coverage</h3>
    <div id="source-bars" class="space-y-2.5"></div>
  </div>
</div>

<!-- Detail modal -->
<div id="modal" class="modal-bg" onclick="if(event.target===this)closeModal()">
  <div class="modal-box">
    <div class="flex justify-between items-start mb-4">
      <div><h2 class="text-base font-bold text-white" id="m-title"></h2><p class="text-xs text-slate-500 mt-0.5" id="m-sub"></p></div>
      <button onclick="closeModal()" class="text-slate-400 hover:text-white text-xl px-2">✕</button>
    </div>
    <div id="m-body"></div>
  </div>
</div>
<div id="toast" class="toast">✓ Copied</div>

<script>
const BASE = 'app://localhost';
let sPage=1, bPage=1;

function switchTab(name,btn){
  document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
  document.querySelectorAll('.panel').forEach(p=>p.classList.remove('active'));
  btn.classList.add('active');
  document.getElementById('panel-'+name).classList.add('active');
  if(name==='stats') loadStats();
  if(name==='browse') doBrowse(1);
}

document.getElementById('s-conf').oninput=function(){document.getElementById('conf-val').textContent=parseFloat(this.value).toFixed(2);};

async function api(path){
  try{const r=await fetch(BASE+path);return await r.json();}
  catch(e){return {error:String(e)};}
}

async function doSearch(page){
  sPage=page||1;
  const p=new URLSearchParams({q:$('s-q').value.trim(),source:document.querySelector('input[name=src]:checked').value,min_confidence:$('s-conf').value,sort:$('s-sort').value,page:sPage,page_size:25});
  if($('s-miny').value)p.set('min_yield',$('s-miny').value);
  if($('s-maxy').value)p.set('max_yield',$('s-maxy').value);
  if($('s-solvent').value.trim())p.set('solvent',$('s-solvent').value.trim());
  if($('s-catalyst').value.trim())p.set('catalyst',$('s-catalyst').value.trim());
  $('s-results').innerHTML='<p class="text-slate-500 text-xs text-center py-8">Searching...</p>';
  const d=await api('/api/search?'+p);
  if(d.error){$('s-results').innerHTML=`<div class="card rounded-xl p-4 text-red-400 text-xs">Error: ${d.error}</div>`;return;}
  $('s-count').textContent=`${d.total.toLocaleString()} result${d.total!==1?'s':''}`;
  $('s-results').innerHTML=d.results.map(r=>card(r)).join('');
  pages(d.page,d.total_pages,'s-pages-top','s-pages-bot','doSearch');
}

function card(r){
  const isOrd=r.source==='ord';
  const badge=isOrd?'<span class="badge-ord text-xs px-2 py-0.5 rounded-full font-semibold">ORD · Expert</span>':'<span class="badge-pat text-xs px-2 py-0.5 rounded-full font-semibold">Patent · LLM</span>';
  const conf=r.confidence!=null?`<span class="text-xs ${r.confidence>=0.7?'text-green-400':r.confidence>=0.4?'text-yellow-400':'text-red-400'}">${(r.confidence*100).toFixed(0)}%</span>`:'';
  const yld=r.yield_percent!=null?`<span class="text-white font-bold text-sm">${r.yield_percent.toFixed(1)}%</span><span class="text-slate-500 text-xs ml-1">yield</span>`:'';
  const tmp=r.temperature_celsius!=null?`<span class="text-slate-300 text-sm ml-3">${r.temperature_celsius.toFixed(0)}°C</span>`:'';
  const f=(label,val,ml=90)=>val?`<div class="mt-1.5"><span class="text-slate-500 text-xs">${label} · </span><span class="chip" onclick="event.stopPropagation();cp('${esc(val)}')" title="${esc(val)}">${val.slice(0,ml)}${val.length>ml?'…':''}</span></div>`:'';
  const notes=r.notes?`<p class="text-slate-400 text-xs mt-2 leading-relaxed">${r.notes.slice(0,220)}${r.notes.length>220?'…':''}</p>`:'';
  return `<div class="card rounded-xl p-3.5 hover:border-indigo-800 transition-colors cursor-pointer" onclick="openDetail('${esc(r.reaction_id)}')">
    <div class="flex items-center justify-between flex-wrap gap-2 mb-1.5">
      <div class="flex items-center gap-2">${badge}${conf}<span class="text-slate-700 text-xs font-mono">${(r.reaction_id||'').slice(0,22)}</span></div>
      <div>${yld}${tmp}</div>
    </div>
    ${f('Product',r.product_smiles)}${f('SMARTS',r.reaction_smarts,110)}${f('Solvent',r.solvent,60)}${f('Catalyst',r.catalyst,80)}${notes}
  </div>`;
}

async function doBrowse(page){
  bPage=page||1;
  const ps=parseInt($('b-ps').value)||25;
  const d=await api(`/api/search?q=${encodeURIComponent($('b-q').value.trim())}&source=${$('b-src').value}&sort=${$('b-sort').value}&page=${bPage}&page_size=${ps}`);
  if(d.error)return;
  $('b-count').textContent=`${d.total.toLocaleString()} reactions`;
  const off=(bPage-1)*ps;
  $('b-tbody').innerHTML=d.results.map((r,i)=>{
    const isOrd=r.source==='ord';
    const badge=isOrd?'<span class="badge-ord px-1.5 py-0.5 rounded font-semibold">ORD</span>':'<span class="badge-pat px-1.5 py-0.5 rounded font-semibold">Pat</span>';
    const conf=r.confidence!=null?`<span class="${r.confidence>=0.7?'text-green-400':r.confidence>=0.4?'text-yellow-400':'text-red-400'}">${(r.confidence*100).toFixed(0)}%</span>`:'-';
    const chip=v=>v?`<span class="chip" onclick="event.stopPropagation();cp('${esc(v)}')">${v.slice(0,26)}${v.length>26?'…':''}</span>`:'-';
    return `<tr class="trow border-t border-[#21293d]" onclick="openDetail('${esc(r.reaction_id)}')">
      <td class="px-3 py-2 text-slate-600">${off+i+1}</td><td class="px-3 py-2">${badge}</td><td class="px-3 py-2">${conf}</td>
      <td class="px-3 py-2">${chip(r.product_smiles)}</td>
      <td class="px-3 py-2 text-white font-semibold">${r.yield_percent!=null?r.yield_percent.toFixed(1)+'%':'-'}</td>
      <td class="px-3 py-2 text-slate-300">${r.temperature_celsius!=null?r.temperature_celsius.toFixed(0)+'°C':'-'}</td>
      <td class="px-3 py-2">${chip(r.solvent)}</td><td class="px-3 py-2">${chip(r.catalyst)}</td>
      <td class="px-3 py-2 font-mono text-indigo-400">${r.reaction_smarts?(r.reaction_smarts.slice(0,38)+(r.reaction_smarts.length>38?'…':'')):'-'}</td>
    </tr>`;
  }).join('');
  pages(d.page,d.total_pages,'b-pages-top','b-pages-bot','doBrowse');
}

async function openDetail(rid){
  const r=await api('/api/reaction/'+encodeURIComponent(rid));
  if(r.error)return;
  $('m-title').textContent=r.reaction_id||'Reaction';
  $('m-sub').textContent=(r.source==='ord'?'ORD Expert':'Patent LLM')+' · conf: '+(r.confidence!=null?(r.confidence*100).toFixed(0)+'%':'—');
  const fld=(lbl,val,mono=false)=>val?`<div class="mb-3"><p class="text-xs text-slate-500 uppercase tracking-widest font-semibold mb-1">${lbl}</p><p class="${mono?'font-mono text-indigo-300 text-xs bg-[#0d1117] p-2 rounded-lg break-all':'text-sm text-slate-200 break-all'}">${val}</p></div>`:'';
  const clk=(lbl,val)=>val?`<div class="mb-3"><p class="text-xs text-slate-500 uppercase tracking-widest font-semibold mb-1">${lbl}</p><span class="chip" onclick="cp('${esc(val)}')">${val}</span></div>`:'';
  const num=(lbl,val,u='')=>val!=null?`<div class="text-center"><p class="text-2xl font-black text-white">${val}${u}</p><p class="text-xs text-slate-500 mt-0.5">${lbl}</p></div>`:'';
  const rcts=(r.reactants||[]).map(s=>`<span class="chip mr-1 mb-1" onclick="cp('${esc(s)}')">${s}</span>`).join('');
  $('m-body').innerHTML=`
    <div class="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4 bg-[#0d1117] rounded-xl p-3">
      ${num('Yield',r.yield_percent!=null?r.yield_percent.toFixed(1):null,'%')}
      ${num('Temp °C',r.temperature_celsius!=null?r.temperature_celsius.toFixed(0):null)}
      ${num('Time h',r.time_hours!=null?r.time_hours.toFixed(1):null)}
      ${num('Confidence',r.confidence!=null?(r.confidence*100).toFixed(0):null,'%')}
    </div>
    ${fld('Reaction SMARTS',r.reaction_smarts,true)}
    ${clk('Product SMILES',r.product_smiles)}
    ${rcts?`<div class="mb-3"><p class="text-xs text-slate-500 uppercase tracking-widest font-semibold mb-1">Reactants</p>${rcts}</div>`:''}
    ${clk('Solvent',r.solvent)}${clk('Catalyst',r.catalyst)}
    ${fld('Mechanism',r.mechanism_text)}${fld('Notes',r.notes)}
    ${r.patent_id?`<div class="mt-3 pt-3 border-t border-[#21293d]"><p class="text-xs text-slate-600">Paper: ${r.patent_id}</p></div>`:''}`;
  $('modal').classList.add('open');
}
function closeModal(){$('modal').classList.remove('open');}
document.addEventListener('keydown',e=>{if(e.key==='Escape')closeModal();});

async function loadStats(){
  const [s,y]=await Promise.all([api('/api/stats'),api('/api/yield_dist')]);
  if(s.error)return;
  $('d-total').textContent=s.total.toLocaleString();$('d-ord').textContent=s.ord.toLocaleString();
  $('d-pat').textContent=s.patent.toLocaleString();$('d-yld').textContent=s.with_yield.toLocaleString();
  if(s.db_path)$('db-badge').textContent=s.db_path;
  $('d-details').innerHTML=[['High Confidence ≥0.6',s.high_confidence?.toLocaleString()],['Avg Yield',s.avg_yield!=null?s.avg_yield+'%':'—'],['Solvents',s.distinct_solvents?.toLocaleString()],['Catalysts',s.distinct_catalysts?.toLocaleString()]].map(([k,v])=>`<div class="flex justify-between text-xs"><span class="text-slate-500">${k}</span><span class="text-white font-semibold">${v}</span></div>`).join('');
  const tot=s.total||1;
  $('source-bars').innerHTML=[['ORD Expert',s.ord,'4ade80'],['Patent LLM',s.patent,'c084fc'],['High Confidence',s.high_confidence,'38bdf8'],['With Yield',s.with_yield,'fb923c']].map(([l,v,c])=>`<div><div class="flex justify-between text-xs text-slate-400 mb-1"><span>${l}</span><span style="color:#${c}">${v?.toLocaleString()||0}</span></div><div class="h-1.5 rounded-full" style="background:#1e2a3a"><div class="h-full rounded-full" style="background:#${c};width:${Math.round((v/tot)*100)}%"></div></div></div>`).join('');
  if(y?.values){const mx=Math.max(...y.values,1);$('yield-chart').innerHTML=y.values.map((v,i)=>`<div class="flex-1 flex flex-col items-center gap-0.5"><div class="yield-bar" style="height:${Math.round((v/mx)*66)+2}px" title="${y.labels[i]}: ${v}"></div><p style="font-size:.5rem;color:#475569">${y.labels[i].split('-')[0]}</p></div>`).join('');}
}

function pages(page,total,topId,botId,fn){
  if(total<=1){[topId,botId].forEach(id=>{if($(id))$(id).innerHTML='';});return;}
  const h=`<button onclick="${fn}(${page-1})" ${page<=1?'disabled':''} class="px-2.5 py-1 rounded text-xs border border-[#21293d] text-slate-400 hover:text-white disabled:opacity-30">←</button><span class="px-2 text-xs text-slate-400">${page}/${total}</span><button onclick="${fn}(${page+1})" ${page>=total?'disabled':''} class="px-2.5 py-1 rounded text-xs border border-[#21293d] text-slate-400 hover:text-white disabled:opacity-30">→</button>`;
  [topId,botId].forEach(id=>{if($(id))$(id).innerHTML=h;});
}

async function exportCSV(){
  const d=await api('/api/export?q='+encodeURIComponent($('s-q').value.trim())+'&source='+document.querySelector('input[name=src]:checked').value);
  if(d.error)return;
  const blob=new Blob([d.csv],{type:'text/csv'});
  const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='reactions.csv';a.click();
}

function cp(t){navigator.clipboard.writeText(t).then(()=>{const x=$('toast');x.style.display='block';setTimeout(()=>x.style.display='none',1600);}).catch(()=>{});}
function esc(s){if(!s)return'';return String(s).replace(/\\/g,'\\\\').replace(/'/g,"\\'").replace(/"/g,'&quot;');}
function $(id){return document.getElementById(id);}
function resetSearch(){$('s-q').value='';document.querySelector('input[name=src][value=all]').checked=true;$('s-miny').value='';$('s-maxy').value='';$('s-conf').value=0;$('conf-val').textContent='0.0';$('s-solvent').value='';$('s-catalyst').value='';$('s-sort').value='confidence';doSearch(1);}
function shutdown(){if(confirm('Close SynAgent?'))fetch(BASE+'/api/shutdown',{method:'POST'}).finally(()=>window.close());}

api('/api/stats').then(s=>{if(s.db_path){$('db-badge').textContent=s.db_path;$('db-badge').classList.remove('hidden');}});
doSearch(1);
</script>
</body>
</html>"""

# ── Custom URL scheme handler — NO SERVER NEEDED ──────────────────────────────
from PySide6.QtCore import QBuffer, QIODevice, QUrl, Qt
from PySide6.QtWebEngineCore import QWebEngineUrlRequestJob, QWebEngineUrlSchemeHandler


class AppHandler(QWebEngineUrlSchemeHandler):
    """Handles all app:// requests directly in Python — no HTTP server."""

    def __init__(self, quit_callback):
        super().__init__()
        self._quit = quit_callback
        self._buffers: dict = {}   # keep QBuffers alive until job completes

    def requestStarted(self, job: QWebEngineUrlRequestJob):
        url = job.requestUrl()
        path = url.path()
        query_str = url.query()

        try:
            mime, data = self._handle(path, query_str, job.requestMethod())
        except Exception as e:
            job.fail(QWebEngineUrlRequestJob.Error.RequestFailed)
            return

        if mime is None:
            job.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)
            return

        buf = QBuffer()
        buf.open(QIODevice.OpenModeFlag.ReadWrite)
        buf.write(data)
        buf.seek(0)

        key = id(job)
        self._buffers[key] = buf
        job.destroyed.connect(lambda: self._buffers.pop(key, None))
        job.reply(mime, buf)

    def _handle(self, path: str, query_str: str, method: bytes):
        qs = parse_qs(query_str)
        g = lambda k, d="": qs.get(k, [d])[0]

        # ── Static assets ────────────────────────────────────────────────
        if path in ("/", ""):
            return b"text/html; charset=utf-8", HTML.encode("utf-8")

        if path == "/tailwind.js":
            return b"application/javascript", TAILWIND_JS

        # ── API ──────────────────────────────────────────────────────────
        if path == "/api/stats":
            return b"application/json", json.dumps(db_stats()).encode()

        if path == "/api/search":
            result = db_search(
                q=unquote(g("q")),
                source=g("source", "all"),
                min_yield=float(g("min_yield")) if g("min_yield") else None,
                max_yield=float(g("max_yield")) if g("max_yield") else None,
                min_confidence=g("min_confidence", "0"),
                solvent=unquote(g("solvent")),
                catalyst=unquote(g("catalyst")),
                sort=g("sort", "confidence"),
                page=int(g("page", "1")),
                page_size=int(g("page_size", "25")),
            )
            return b"application/json", json.dumps(result).encode()

        if path.startswith("/api/reaction/"):
            rid = unquote(path[len("/api/reaction/"):])
            return b"application/json", json.dumps(db_reaction(rid)).encode()

        if path == "/api/yield_dist":
            return b"application/json", json.dumps(db_yield_dist()).encode()

        if path == "/api/export":
            csv_bytes = db_export_csv(
                q=unquote(g("q")),
                source=g("source", "all"),
                min_yield=float(g("min_yield")) if g("min_yield") else None,
                min_confidence=g("min_confidence", "0"),
            )
            # Return as JSON with csv key so JS can blob-download it
            payload = json.dumps({"csv": csv_bytes.decode("utf-8", errors="replace")})
            return b"application/json", payload.encode()

        if path == "/api/shutdown":
            self._quit()
            return b"application/json", b'{"ok":true}'

        return None, None


# ── Main entry point ──────────────────────────────────────────────────────────
def main():
    from PySide6.QtWidgets import QApplication, QMainWindow
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWebEngineCore import QWebEngineSettings, QWebEngineProfile

    app_qt = QApplication(sys.argv)
    app_qt.setApplicationName("SynAgent")

    win = QMainWindow()
    win.setWindowTitle("SynAgent · Reaction Database")
    win.resize(1440, 900)
    win.setMinimumSize(900, 600)
    win.setStyleSheet("QMainWindow { background: #0d1117; }")

    view = QWebEngineView()
    settings = view.settings()
    settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
    settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)

    # Register the scheme handler
    handler = AppHandler(quit_callback=app_qt.quit)
    profile = QWebEngineProfile.defaultProfile()
    profile.installUrlSchemeHandler(b"app", handler)

    win.setCentralWidget(view)
    view.setUrl(QUrl("app://localhost/"))
    win.show()

    db = _find_db()
    if not db:
        # Show warning overlay via JS after page loads
        def warn():
            view.page().runJavaScript(
                'document.body.innerHTML = \'<div style="display:flex;align-items:center;'
                'justify-content:center;height:100vh;background:#0d1117;color:#f87171;'
                'font-family:system-ui;font-size:1.1rem;flex-direction:column;gap:1rem">'
                '<span style=\\"font-size:3rem\\">⚠️</span>'
                '<b>Database not found</b>'
                '<span style=\\"color:#64748b;font-size:.9rem\\">Expected: db/patent_pipeline.db next to this app</span>'
                '</div>\';'
            )
        view.loadFinished.connect(lambda _: warn())

    sys.exit(app_qt.exec())


if __name__ == "__main__":
    main()
