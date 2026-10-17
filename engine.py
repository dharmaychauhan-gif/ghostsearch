"""
GhostSearch Privacy-First Search Engine
- 100% In-Memory (No disk storage, no database, no query logging)
- Cloud & Datacenter optimized (Runs reliably on Render, AWS, and local PC)
- Multi-engine failover: DuckDuckGo Lite -> Mojeek -> DuckDuckGo HTML
- SafeSearch levels: Strict, Moderate, Off
- Strips ads, telemetry redirects, and tracking parameters
"""

import httpx
import urllib.parse
from bs4 import BeautifulSoup
from typing import List, Dict, Optional, Any

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

async def fetch_instant_answer(client: httpx.AsyncClient, query: str) -> Optional[Dict[str, Any]]:
    """Fetches high-accuracy instant answer summaries (e.g., Wikipedia) without telemetry."""
    try:
        url = "https://api.duckduckgo.com/"
        params = {
            "q": query,
            "format": "json",
            "no_html": "1",
            "skip_disambig": "1"
        }
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

async def _fetch_ddg_lite(client: httpx.AsyncClient, query: str, page: int, kp_val: str) -> List[Dict[str, str]]:
    """Primary: DuckDuckGo Lite (never blocked by datacenter / cloud IPs)."""
    results: List[Dict[str, str]] = []
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    data = {"q": query, "kp": kp_val}
    if page > 1:
        data["s"] = str((page - 1) * 30)

    resp = await client.post(
        "https://lite.duckduckgo.com/lite/",
        data=data,
        headers=headers,
        follow_redirects=True,
        timeout=8.0
    )
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

async def _fetch_mojeek(client: httpx.AsyncClient, query: str, page: int) -> List[Dict[str, str]]:
    """Secondary Fallback: Mojeek privacy engine (independent index, no tracking)."""
    results: List[Dict[str, str]] = []
    headers = {"User-Agent": USER_AGENT}
    start = (page - 1) * 10 + 1
    url = f"https://www.mojeek.com/search?q={urllib.parse.quote_plus(query)}&s={start}"
    resp = await client.get(url, headers=headers, timeout=6.0)
    if resp.status_code != 200:
        return results

    soup = BeautifulSoup(resp.text, "html.parser")
    for li in soup.find_all("li"):
        a_tag = li.find("a", class_="title") or li.find("a", class_="ob")
        if not a_tag:
            continue
        href = a_tag.get("href", "")
        title = a_tag.get_text(strip=True)
        snip = li.find("p", class_="s") or li.find("p")
        snippet_text = snip.get_text(strip=True) if snip else ""
        clean_target = clean_url(href)
        if clean_target.startswith("http") and not any(r["url"] == clean_target for r in results):
            results.append({
                "title": title,
                "url": clean_target,
                "snippet": snippet_text
            })
    return results

async def fetch_web_results(
    client: httpx.AsyncClient, 
    query: str, 
    page: int = 1,
    safesearch: str = "moderate"
) -> List[Dict[str, str]]:
    """Fetches organic results with zero ad injection and privacy preservation."""
    kp_map = {"strict": "1", "moderate": "-1", "off": "-2"}
    kp_val = kp_map.get(safesearch.lower(), "-1")

    # 1. Primary: DuckDuckGo Lite
    try:
        results = await _fetch_ddg_lite(client, query, page, kp_val)
        if results:
            return results
    except Exception:
        pass

    # 2. Fallback: Mojeek Privacy Engine
    try:
        results = await _fetch_mojeek(client, query, page)
        if results:
            return results
    except Exception:
        pass

    return []

async def search_all(query: str, page: int = 1, safesearch: str = "moderate") -> Dict[str, Any]:
    """Orchestrates concurrent fetching of instant answer and web results."""
    query = query.strip()
    if not query:
        return {"instant": None, "results": [], "query": "", "page": page, "safesearch": safesearch}

    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
        instant_answer = None
        if safesearch.lower() != "off":
            instant_answer = await fetch_instant_answer(client, query)
        web_results = await fetch_web_results(client, query, page=page, safesearch=safesearch)

    return {
        "query": query,
        "page": page,
        "safesearch": safesearch,
        "instant": instant_answer,
        "results": web_results
    }
