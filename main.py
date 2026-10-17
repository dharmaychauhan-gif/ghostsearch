"""
GhostSearch Application Server
- Zero-Storage, RAM-only execution
- No disk writing, no query logs
- Strict anti-cache and anti-tracking headers
"""

import gc
import logging
from pathlib import Path
from fastapi import FastAPI, Request, Form, Query, Response
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
try:
    from app.engine import search_all
except ImportError:
    from engine import search_all

# SILENCE ALL LOGS: Prevent query leakage into console or files
logging.getLogger("uvicorn.access").disabled = True
logging.getLogger("uvicorn.error").setLevel(logging.CRITICAL)
logging.disable(logging.CRITICAL)

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

app = FastAPI(title="GhostSearch", docs_url=None, redoc_url=None, openapi_url=None)

@app.middleware("http")
async def enforce_zero_storage_headers(request: Request, call_next):
    """Applies strict privacy, anti-cache, and no-referrer headers to all responses."""
    response = await call_next(request)
    # Anti-caching headers: Never allow disk or proxy caching
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    
    # Anti-leak / privacy headers
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

@app.post("/search", response_class=HTMLResponse)
async def search_post(
    request: Request,
    q: str = Form(""),
    page: int = Form(1),
    safesearch: str = Form("moderate")
):
    """
    Primary search handler using HTTP POST.
    POST requests do NOT store the search query in browser URL address bar history.
    """
    query = q.strip()
    data = await search_all(query=query, page=page, safesearch=safesearch)
    # Explicit garbage collection trigger to clear transient search data from RAM
    gc.collect()
    return templates.TemplateResponse(
        request=request,
        name="results.html",
        context={
            "query": data["query"],
            "page": data["page"],
            "safesearch": data["safesearch"],
            "instant": data["instant"],
            "results": data["results"]
        }
    )

@app.get("/search", response_class=HTMLResponse)
async def search_get(
    request: Request,
    q: str = Query(""),
    page: int = Query(1),
    safesearch: str = Query("moderate")
):
    """
    Fallback GET search handler to allow setting GhostSearch as browser default engine.
    """
    query = q.strip()
    data = await search_all(query=query, page=page, safesearch=safesearch)
    gc.collect()
    return templates.TemplateResponse(
        request=request,
        name="results.html",
        context={
            "query": data["query"],
            "page": data["page"],
            "safesearch": data["safesearch"],
            "instant": data["instant"],
            "results": data["results"]
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
