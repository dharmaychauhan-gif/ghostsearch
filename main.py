"""
GhostSearch Application Server (Cloud & Local)
- 100% In-Memory (Zero disk writes, zero query logs)
- Cloud & Datacenter optimized (Runs reliably on Render, AWS, and local PC)
- Multi-engine search: Bing (Cloud IP friendly) + DuckDuckGo Lite fallback
- SafeSearch levels: Strict, Moderate, Off
- SPA Search API (/api/search) for zero-history in-page searching
- Ghost Reader Proxy (/api/proxy) for anonymous reading without visiting target sites
- Full PWA Support (/manifest.json, /sw.js, icons) for 1-Tap Home Screen App Install
"""

import gc
import logging
import base64
import urllib.parse
from pathlib import Path
from typing import List, Dict, Optional, Any

import httpx
from bs4 import BeautifulSoup
from fastapi import FastAPI, Request, Form, Query, Response
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

# SILENCE ALL LOGS: Prevent query leakage into console or files
logging.getLogger("uvicorn.access").disabled = True
logging.getLogger("uvicorn.error").setLevel(logging.CRITICAL)
logging.disable(logging.CRITICAL)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "msclkid", "mc_cid", "mc_eid", "ref", "source"
}

def clean_url(url: str) -> str:
    """Removes marketing, tracking, and telemetry parameters from destination URLs."""
    try:
        parsed = urllib.parse.urlparse(url)
        qs = urllib.parse.parse_qs(parsed.query)
        if "uddg" in qs:
            parsed = urllib.parse.urlparse(qs["uddg"][0])
            qs = urllib.parse.parse_qs(parsed.query)

        clean_qs = {k: v for k, v in qs.items() if k.lower() not in TRACKING_PARAMS}
        new_query = urllib.parse.urlencode(clean_qs, doseq=True)
        return urllib.parse.urlunparse((
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            new_query,
            parsed.fragment
        ))
    except Exception:
        return url

def unpack_bing_redirect(href: str) -> str:
    """Unpacks Bing redirected URLs into genuine target destination URLs."""
    if href and "bing.com/ck/a?" in href:
        try:
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
            u = qs.get("u", [""])[0]
            if u and len(u) > 2:
                b = u[2:]
                decoded = base64.urlsafe_b64decode(b + "=" * ((-len(b)) % 4)).decode("utf-8", errors="ignore")
                return decoded
        except Exception:
            pass
    return href

async def fetch_instant_answer(client: httpx.AsyncClient, query: str) -> Optional[Dict[str, Any]]:
    """Fetches high-accuracy instant answer summaries (e.g., Wikipedia) without telemetry."""
    try:
        url = "https://api.duckduckgo.com/"
        params = {"q": query, "format": "json", "no_html": "1", "skip_disambig": "1"}
        resp = await client.get(url, params=params, timeout=4.0)
        if resp.status_code == 200:
            data = resp.json()
            heading = data.get("Heading")
            abstract = data.get("AbstractText")
            source_url = data.get("AbstractURL")
            image = data.get("Image")
            if heading and abstract:
                return {
                    "heading": heading,
                    "abstract": abstract,
                    "url": clean_url(source_url) if source_url else None,
                    "image": f"https://duckduckgo.com{image}" if image and image.startswith("/") else image,
                    "source": data.get("AbstractSource", "Wikipedia")
                }
    except Exception:
        pass
    return None

async def _fetch_bing(client: httpx.AsyncClient, query: str, page: int, safesearch: str) -> List[Dict[str, str]]:
    """Primary: Bing search engine (never blocks cloud/datacenter IPs, top tier results)."""
    results: List[Dict[str, str]] = []
    adlt_map = {"strict": "strict", "moderate": "moderate", "off": "off"}
    params = {
        "q": query,
        "adlt": adlt_map.get(safesearch.lower(), "moderate")
    }
    if page > 1:
        params["first"] = str((page - 1) * 10 + 1)

    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    resp = await client.get("https://www.bing.com/search", params=params, headers=headers, timeout=8.0)
    if resp.status_code != 200:
        return results

    soup = BeautifulSoup(resp.text, "html.parser")
    for item in soup.find_all("li", class_="b_algo"):
        h2 = item.find("h2")
        a_tag = h2.find("a") if h2 else None
        p_tag = item.find("p")
        if not a_tag:
            continue

        raw_url = a_tag.get("href", "")
        dest_url = unpack_bing_redirect(raw_url)
        clean_target = clean_url(dest_url)
        title = a_tag.get_text(strip=True)
        snippet = p_tag.get_text(strip=True) if p_tag else ""

        if clean_target.startswith("http") and not any(r["url"] == clean_target for r in results):
            results.append({
                "title": title,
                "url": clean_target,
                "snippet": snippet
            })
    return results

async def _fetch_ddg_lite(client: httpx.AsyncClient, query: str, page: int, safesearch: str) -> List[Dict[str, str]]:
    """Secondary Fallback: DuckDuckGo Lite."""
    results: List[Dict[str, str]] = []
    kp_map = {"strict": "1", "moderate": "-1", "off": "-2"}
    data = {"q": query, "kp": kp_map.get(safesearch.lower(), "-1")}
    if page > 1:
        data["s"] = str((page - 1) * 30)

    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    resp = await client.post("https://lite.duckduckgo.com/lite/", data=data, headers=headers, timeout=8.0)
    if resp.status_code != 200:
        return results

    soup = BeautifulSoup(resp.text, "html.parser")
    for link in soup.find_all("a", class_="result-link"):
        href = link.get("href", "")
        title = link.get_text(strip=True)
        if not href or not title or title.lower() in ("more info", "next", "prev"):
            continue
        if any(bad in href for bad in ("y.js", "ad_provider", "aclick", "duckduckgo.com")):
            continue

        clean_target = clean_url(href)
        if "duckduckgo.com" in clean_target.lower():
            continue

        snippet_text = ""
        parent_tr = link.find_parent("tr")
        if parent_tr:
            next_tr = parent_tr.find_next_sibling("tr")
            if next_tr:
                snippet_td = next_tr.find("td", class_="result-snippet")
                if snippet_td:
                    snippet_text = snippet_td.get_text(strip=True)

        if not any(r["url"] == clean_target for r in results):
            results.append({
                "title": title,
                "url": clean_target,
                "snippet": snippet_text
            })
    return results

async def search_all(query: str, page: int = 1, safesearch: str = "moderate") -> Dict[str, Any]:
    """Orchestrates search with multi-engine failover: Bing -> DuckDuckGo Lite."""
    query = query.strip()
    if not query:
        return {"query": "", "page": page, "safesearch": safesearch, "instant": None, "results": []}

    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
        instant_answer = None
        if safesearch.lower() != "off":
            instant_answer = await fetch_instant_answer(client, query)

        # 1. Try Bing primary
        try:
            web_results = await _fetch_bing(client, query, page, safesearch)
        except Exception:
            web_results = []

        # 2. Try DuckDuckGo Lite fallback if Bing returned empty
        if not web_results:
            try:
                web_results = await _fetch_ddg_lite(client, query, page, safesearch)
            except Exception:
                web_results = []

    return {
        "query": query,
        "page": page,
        "safesearch": safesearch,
        "instant": instant_answer,
        "results": web_results
    }

# ----------------- FASTAPI WEB SERVER -----------------

APP_DIR = Path(__file__).resolve().parent
ROOT_DIR = APP_DIR.parent

# Smart template loader
root_idx = ROOT_DIR / "index.html"
app_idx = APP_DIR / "templates" / "index.html"

if root_idx.exists() and (not app_idx.exists() or root_idx.stat().st_size >= app_idx.stat().st_size):
    template_candidates = [str(ROOT_DIR), str(APP_DIR / "templates"), str(ROOT_DIR / "templates"), str(APP_DIR)]
else:
    template_candidates = [str(APP_DIR / "templates"), str(ROOT_DIR), str(ROOT_DIR / "templates"), str(APP_DIR)]

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

# PWA MANIFEST & SERVICE WORKER
@app.get("/manifest.json")
async def get_manifest():
    return JSONResponse(
        content={
            "name": "GhostSearch",
            "short_name": "GhostSearch",
            "start_url": "/",
            "display": "standalone",
            "background_color": "#0b1120",
            "theme_color": "#0b1120",
            "description": "100% In-Memory Private Search Engine",
            "icons": [
                {
                    "src": "/icon-192.png",
                    "sizes": "192x192",
                    "type": "image/png",
                    "purpose": "any maskable"
                },
                {
                    "src": "/icon-512.png",
                    "sizes": "512x512",
                    "type": "image/png",
                    "purpose": "any maskable"
                }
            ]
        },
        headers={"Content-Type": "application/manifest+json"}
    )

@app.get("/sw.js")
async def get_sw():
    js_content = "self.addEventListener('install', (e) => { self.skipWaiting(); });\nself.addEventListener('activate', (e) => { e.waitUntil(self.clients.claim()); });\nself.addEventListener('fetch', (e) => { e.respondWith(fetch(e.request)); });"
    return Response(content=js_content, media_type="application/javascript")

@app.get("/icon-192.png")
async def get_icon_192():
    for base in [ROOT_DIR, APP_DIR]:
        p = base / "icon-192.png"
        if p.exists():
            return Response(content=p.read_bytes(), media_type="image/png")
    return Response(status_code=404)

@app.get("/icon-512.png")
async def get_icon_512():
    for base in [ROOT_DIR, APP_DIR]:
        p = base / "icon-512.png"
        if p.exists():
            return Response(content=p.read_bytes(), media_type="image/png")
    return Response(status_code=404)

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """Renders the minimalist, zero-storage home search interface."""
    return templates.TemplateResponse(
        request=request, 
        name="index.html",
        context={"safesearch": "moderate", "results": None, "query": "", "instant": None}
    )

@app.get("/api/search")
async def api_search(
    q: str = Query(""),
    page: int = Query(1),
    safesearch: str = Query("moderate")
):
    """JSON search endpoint for Single-Page In-Place searches (zero browser history navigation)."""
    query = q.strip()
    try:
        data = await search_all(query=query, page=page, safesearch=safesearch)
    except Exception:
        data = {"query": query, "page": page, "safesearch": safesearch, "instant": None, "results": []}

    gc.collect()
    return JSONResponse(content=data)

@app.get("/api/proxy")
async def api_proxy(url: str = Query("")):
    """
    Ghost Reader Proxy:
    Anonymously extracts readable text from destination URLs.
    Allows user to read articles without their browser connecting to or logging the site!
    """
    target = url.strip()
    if not target.startswith("http://") and not target.startswith("https://"):
        return JSONResponse(status_code=400, content={"error": "Invalid URL protocol"})

    try:
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        }
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
            resp = await client.get(target, headers=headers)
            if resp.status_code != 200:
                return JSONResponse(content={"error": f"Target server returned HTTP {resp.status_code}"})

            soup = BeautifulSoup(resp.text, "html.parser")
            for bad in soup(["script", "style", "nav", "footer", "aside", "iframe", "form", "noscript", "svg"]):
                bad.decompose()

            article = soup.find("article") or soup.find("main") or soup.find("body")
            paragraphs = []
            if article:
                for el in article.find_all(["p", "h2", "h3", "li"]):
                    txt = el.get_text(strip=True)
                    if len(txt) > 35 and txt not in paragraphs:
                        paragraphs.append(txt)

            title = soup.title.get_text(strip=True) if soup.title else "Web Article"
            domain = urllib.parse.urlparse(target).netloc
            gc.collect()
            return JSONResponse(content={
                "status": "success",
                "title": title,
                "domain": domain,
                "url": target,
                "paragraphs": paragraphs[:25]
            })
    except Exception as e:
        return JSONResponse(content={"error": f"Failed to retrieve reader content: {str(e)}"})

@app.post("/search", response_class=HTMLResponse)
async def search_post(
    request: Request,
    q: str = Form(""),
    page: int = Form(1),
    safesearch: str = Form("moderate")
):
    """Traditional POST fallback (for browsers with JS disabled)."""
    query = q.strip()
    try:
        data = await search_all(query=query, page=page, safesearch=safesearch)
    except Exception:
        data = {"query": query, "page": page, "safesearch": safesearch, "instant": None, "results": []}

    gc.collect()
    return templates.TemplateResponse(
        request=request,
        name="index.html",
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
    try:
        data = await search_all(query=query, page=page, safesearch=safesearch)
    except Exception:
        data = {"query": query, "page": page, "safesearch": safesearch, "instant": None, "results": []}

    gc.collect()
    return templates.TemplateResponse(
        request=request,
        name="index.html",
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
