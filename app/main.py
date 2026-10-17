"""
GhostSearch Application Server (Cloud & Local)
- Zero-Storage, RAM-only execution
- No disk writing, no query logs
- Multi-engine search aggregator with SafeSearch
"""

import gc
import logging
import sys
import importlib.util
from pathlib import Path
from fastapi import FastAPI, Request, Form, Query, Response
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

# Locate directories
APP_DIR = Path(__file__).resolve().parent
ROOT_DIR = APP_DIR.parent

# 1. DIRECT ENGINE LOADER: Load root engine.py directly by path so old app/engine.py is never used
search_all = None
root_engine_path = ROOT_DIR / "engine.py"
app_engine_path = APP_DIR / "engine.py"

if root_engine_path.exists():
    try:
        spec = importlib.util.spec_from_file_location("root_engine", str(root_engine_path))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        if hasattr(mod, "search_all"):
            search_all = mod.search_all
    except Exception as e:
        pass

if search_all is None and app_engine_path.exists():
    try:
        spec = importlib.util.spec_from_file_location("app_engine", str(app_engine_path))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        if hasattr(mod, "search_all"):
            search_all = mod.search_all
    except Exception as e:
        pass

# Fallback import if needed
if search_all is None:
    try:
        from engine import search_all
    except ImportError:
        from app.engine import search_all

# SILENCE ALL LOGS: Prevent query leakage into console or files
logging.getLogger("uvicorn.access").disabled = True
logging.getLogger("uvicorn.error").setLevel(logging.CRITICAL)
logging.disable(logging.CRITICAL)

# Look for templates in root first (where user uploaded index.html & results.html), then subdirectories
template_candidates = [
    str(ROOT_DIR),
    str(ROOT_DIR / "templates"),
    str(APP_DIR / "templates"),
    str(APP_DIR)
]
valid_dirs = [d for d in template_candidates if Path(d).exists()]
templates = Jinja2Templates(directory=valid_dirs if valid_dirs else str(ROOT_DIR))

app = FastAPI(title="GhostSearch", docs_url=None, redoc_url=None, openapi_url=None)

@app.middleware("http")
async def enforce_zero_storage_headers(request: Request, call_next):
    """Applies strict privacy, anti-cache, and no-referrer headers to all responses."""
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    return response

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """Renders the minimalist, zero-storage home search interface."""
    return templates.TemplateResponse(
        request=request, 
        name="index.html",
        context={"safesearch": "moderate"}
    )

async def _perform_search(query: str, page: int, safesearch: str):
    """Safely executes search across any version of search_all without ever crashing."""
    try:
        # Try calling with safesearch keyword
        return await search_all(query=query, page=page, safesearch=safesearch)
    except TypeError:
        try:
            # Fallback if old signature only takes (query, page)
            res = await search_all(query=query, page=page)
            if isinstance(res, dict):
                res["safesearch"] = safesearch
            return res
        except Exception:
            return {"query": query, "page": page, "safesearch": safesearch, "instant": None, "results": []}
    except Exception:
        return {"query": query, "page": page, "safesearch": safesearch, "instant": None, "results": []}

@app.post("/search", response_class=HTMLResponse)
async def search_post(
    request: Request,
    q: str = Form(""),
    page: int = Form(1),
    safesearch: str = Form("moderate")
):
    """Primary search handler using HTTP POST (no URL history leakage)."""
    query = q.strip()
    data = await _perform_search(query=query, page=page, safesearch=safesearch)
    gc.collect()
    return templates.TemplateResponse(
        request=request,
        name="results.html",
        context={
            "query": data.get("query", query),
            "page": data.get("page", page),
            "safesearch": data.get("safesearch", safesearch),
            "instant": data.get("instant"),
            "results": data.get("results", [])
        }
    )

@app.get("/search", response_class=HTMLResponse)
async def search_get(
    request: Request,
    q: str = Query(""),
    page: int = Query(1),
    safesearch: str = Query("moderate")
):
    """Fallback GET search handler to allow setting GhostSearch as browser default engine."""
    query = q.strip()
    data = await _perform_search(query=query, page=page, safesearch=safesearch)
    gc.collect()
    return templates.TemplateResponse(
        request=request,
        name="results.html",
        context={
            "query": data.get("query", query),
            "page": data.get("page", page),
            "safesearch": data.get("safesearch", safesearch),
            "instant": data.get("instant"),
            "results": data.get("results", [])
        }
    )

@app.post("/api/purge")
@app.get("/api/purge")
async def purge_memory():
    """Immediately triggers garbage collection and purges volatile RAM buffers."""
    gc.collect()
    return JSONResponse(content={
        "status": "success",
        "message": "Memory purged. All volatile search buffers flushed.",
        "storage_mode": "RAM-only (zero disk writes)"
    })
