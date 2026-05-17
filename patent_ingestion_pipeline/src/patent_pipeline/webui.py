from __future__ import annotations

from fastapi import FastAPI, Request, Form, Depends, HTTPException, status
import json
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pathlib import Path
import os

from .database import PatentDatabase

app = FastAPI(title="Patent Parser Review UI")
BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
# mount static files for CSS/JS
app.mount('/static', StaticFiles(directory=str(BASE_DIR / 'static')), name='static')
security = HTTPBasic()


def require_auth(creds: HTTPBasicCredentials = Depends(security)) -> bool:
    ui_user = os.getenv("PATENT_UI_USER")
    ui_pass = os.getenv("PATENT_UI_PASS")
    if not ui_user or not ui_pass:
        # if not configured, allow access (development mode)
        return True
    if creds.username == ui_user and creds.password == ui_pass:
        return True
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")


@app.get("/", response_class=HTMLResponse)
def index(request: Request, db_path: str = "data/patent_pipeline.db", auth: bool = Depends(require_auth)):
    db = PatentDatabase(db_path)
    rows = db.connection.execute("SELECT patent_id, title, reviewed FROM patents ORDER BY reviewed, publication_date DESC LIMIT 200").fetchall()
    db.close()
    return templates.TemplateResponse("index.html", {"request": request, "patents": rows})


@app.post("/enqueue_all")
def web_enqueue_all(db_path: str = "data/patent_pipeline.db", auth: bool = Depends(require_auth)):
    db = PatentDatabase(db_path)
    cur = db.connection.execute("SELECT id FROM raw_documents ORDER BY id")
    count = 0
    for r in cur.fetchall():
        try:
            db.enqueue_raw_document(int(r["id"]))
            count += 1
        except Exception:
            pass
    db.close()
    return RedirectResponse(url='/', status_code=303)


@app.get('/search', response_class=HTMLResponse)
def web_search(request: Request, q: str = '', db_path: str = "data/patent_pipeline.db", auth: bool = Depends(require_auth)):
    db = PatentDatabase(db_path)
    patents = []
    reactions = []
    if q:
        try:
            res = db.search_text(q)
            patents = res.get('patents', [])
            reactions = res.get('reactions', [])
        except Exception:
            pass
    db.close()
    return templates.TemplateResponse('search.html', {"request": request, "q": q, "patents": patents, "reactions": reactions})


@app.get('/batch_review', response_class=HTMLResponse)
def batch_review(request: Request, db_path: str = "data/patent_pipeline.db", auth: bool = Depends(require_auth)):
    db = PatentDatabase(db_path)
    # select reactions that have low or missing confidence
    rows = db.connection.execute("SELECT * FROM reactions WHERE confidence IS NULL OR confidence < 0.6 ORDER BY patent_id LIMIT 200").fetchall()
    db.close()
    return templates.TemplateResponse('batch_review.html', {"request": request, "reactions": rows})


@app.post('/api/active_learning')
def api_active_learning(payload: dict, db_path: str = "data/patent_pipeline.db", auth: bool = Depends(require_auth)):
    # payload: {reaction_id, patent_id, field, old_value, new_value, user}
    rid = payload.get('reaction_id')
    pid = payload.get('patent_id')
    field = payload.get('field')
    old = payload.get('old_value')
    new = payload.get('new_value')
    user = payload.get('user') or 'web'
    db = PatentDatabase(db_path)
    with db.connection:
        db.connection.execute(
            "INSERT INTO active_learning (reaction_id, patent_id, field, old_value, new_value, user) VALUES (?, ?, ?, ?, ?, ?)",
            (rid, pid, field, old, new, user),
        )
        # apply correction to reactions row if applicable
        if field and rid and new is not None:
            # simple update for common fields
            if field in ('product_smiles', 'notes'):
                db.connection.execute(f"UPDATE reactions SET {field} = ? WHERE reaction_id = ?", (new, rid))
    db.close()
    return {"status": "ok"}


@app.get("/patent/{patent_id}", response_class=HTMLResponse)
def view_patent(request: Request, patent_id: str, db_path: str = "data/patent_pipeline.db", auth: bool = Depends(require_auth)):
    db = PatentDatabase(db_path)
    p = db.connection.execute("SELECT * FROM patents WHERE patent_id = ?", (patent_id,)).fetchone()
    reactions = db.connection.execute("SELECT * FROM reactions WHERE patent_id = ?", (patent_id,)).fetchall()
    db.close()
    return templates.TemplateResponse("patent.html", {"request": request, "patent": p, "reactions": reactions})


@app.post("/patent/{patent_id}/approve")
def approve_patent(patent_id: str, db_path: str = "data/patent_pipeline.db", auth: bool = Depends(require_auth)):
    db = PatentDatabase(db_path)
    db.mark_patent_reviewed(patent_id, True)
    db.close()
    return RedirectResponse(url=f"/patent/{patent_id}", status_code=303)


@app.post("/patent/{patent_id}/update")
def update_patent(patent_id: str, title: str = Form(...), abstract: str = Form(""), db_path: str = "data/patent_pipeline.db", auth: bool = Depends(require_auth)):
    db = PatentDatabase(db_path)
    with db.connection:
        db.connection.execute("UPDATE patents SET title = ?, abstract = ? WHERE patent_id = ?", (title, abstract, patent_id))
        # re-index
        row = db.connection.execute("SELECT * FROM patents WHERE patent_id = ?", (patent_id,)).fetchone()
        if row:
            from .models import PatentRecord
            pr = PatentRecord(
                patent_id=row["patent_id"],
                title=row["title"],
                abstract=row["abstract"],
                source_url=row["source_url"],
                raw_text=row["raw_text"],
                reactions=[],
            )
            db._index_patent_fts(pr)
    db.close()
    return RedirectResponse(url=f"/patent/{patent_id}", status_code=303)


@app.post("/patent/{patent_id}/reaction/{reaction_id}/update")
def update_reaction(patent_id: str, reaction_id: str, product_smiles: str = Form(None), yield_percent: str = Form(None), notes: str = Form(None), db_path: str = "data/patent_pipeline.db", auth: bool = Depends(require_auth)):
    db = PatentDatabase(db_path)
    with db.connection:
        db.connection.execute(
            "UPDATE reactions SET product_smiles = ?, yield_percent = ?, notes = ? WHERE reaction_id = ?",
            (product_smiles, float(yield_percent) if yield_percent else None, notes, reaction_id),
        )
        # re-index reaction
        row = db.connection.execute("SELECT * FROM reactions WHERE reaction_id = ?", (reaction_id,)).fetchone()
        if row:
            from .models import ReactionRecord
            rr = ReactionRecord(
                reaction_id=row["reaction_id"],
                patent_id=row["patent_id"],
                reaction_smarts=row["reaction_smarts"],
                reactant_smiles=json.loads(row["reactant_smiles_json"] or "[]"),
                product_smiles=row["product_smiles"],
                yield_percent=row["yield_percent"],
                temperature_celsius=row["temperature_celsius"],
                solvent=row["solvent"],
                catalyst=row["catalyst"],
                time_hours=row["time_hours"],
                mechanism_text=row["mechanism_text"],
                notes=row["notes"],
            )
            db._index_reaction_fts(rr)
    db.close()
    return RedirectResponse(url=f"/patent/{patent_id}", status_code=303)


@app.post('/api/reaction/update')
def api_update_reaction(payload: dict, db_path: str = "data/patent_pipeline.db", auth: bool = Depends(require_auth)):
    # payload: {patent_id,reaction_id,product_smiles,yield_percent,notes}
    patent_id = payload.get('patent_id')
    reaction_id = payload.get('reaction_id')
    product_smiles = payload.get('product_smiles')
    yp = payload.get('yield_percent')
    notes = payload.get('notes')
    from .chem_ner import normalize_smiles
    db = PatentDatabase(db_path)
    # validate/normalize SMILES if provided
    if product_smiles:
        ns = normalize_smiles(product_smiles)
        if ns is None:
            raise HTTPException(status_code=400, detail="Invalid product SMILES")
        product_smiles = ns
    # update reaction row
    with db.connection:
        db.connection.execute(
            "UPDATE reactions SET product_smiles = ?, yield_percent = ?, notes = ? WHERE reaction_id = ?",
            (product_smiles, float(yp) if yp not in (None, '') else None, notes, reaction_id),
        )
        # re-index
        row = db.connection.execute("SELECT * FROM reactions WHERE reaction_id = ?", (reaction_id,)).fetchone()
        if row:
            from .models import ReactionRecord
            rr = ReactionRecord(
                reaction_id=row["reaction_id"],
                patent_id=row["patent_id"],
                reaction_smarts=row["reaction_smarts"],
                reactant_smiles=json.loads(row["reactant_smiles_json"] or "[]"),
                product_smiles=row["product_smiles"],
                yield_percent=row["yield_percent"],
                temperature_celsius=row["temperature_celsius"],
                solvent=row["solvent"],
                catalyst=row["catalyst"],
                time_hours=row["time_hours"],
                mechanism_text=row["mechanism_text"],
                notes=row["notes"],
            )
            db._index_reaction_fts(rr)
    db.close()
    return {"status":"ok"}
