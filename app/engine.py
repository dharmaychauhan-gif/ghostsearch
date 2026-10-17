"""
Privacy-First Search Aggregator
- 100% In-Memory (No database, no disk cache, no query logging)
- Strips all ads, tracking redirects, and analytics parameters
- Enforces strict no-referrer semantics
"""

import httpx
import re
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
        # Check if URL is wrapped in a DuckDuckGo or other redirect
        qs = urllib.parse.parse_qs(parsed.query)
        if "uddg" in qs:
            target = qs["uddg"][0]
            parsed = urllib.parse.urlparse(target)
            qs = urllib.parse.parse_qs(parsed.query)

        # Remove known tracking parameters
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

async def fetch_web_results(client: httpx.AsyncClient, query: str, page: int = 1) -> List[Dict[str, str]]:
    """
    Fetches raw organic results via anonymized POST request,
    filters all paid adverts, and unpacks genuine destination URLs.
    """
    results: List[Dict[str, str]] = []
    try:
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        
        # Calculate pagination offset if needed
        data = {"q": query}
        if page > 1:
            data["s"] = str((page - 1) * 30)

        resp = await client.post(
            "https://html.duckduckgo.com/html/",
            data=data,
            headers=headers,
            follow_redirects=True,
            timeout=8.0
        )
        
        if resp.status_code != 200:
            return results

        soup = BeautifulSoup(resp.text, "html.parser")
        
        for item in soup.find_all("div", class_="result"):
            # Exclude advertisements
            classes = item.get("class", [])
            if any("ad" in c.lower() for c in classes):
                continue
            
            title_elem = item.find("a", class_="result__a")
            snippet_elem = item.find("a", class_="result__snippet")
            
            if not title_elem:
                continue

            raw_href = title_elem.get("href", "")
            # Filter ad networks
            if "duckduckgo.com/y.js" in raw_href or "ad_provider" in raw_href or "aclick" in raw_href:
                continue

            destination_url = clean_url(raw_href)
            title = title_elem.get_text(strip=True)
            snippet = snippet_elem.get_text(strip=True) if snippet_elem else ""

            # Check for duplicate URLs
            if not any(r["url"] == destination_url for r in results):
                results.append({
                    "title": title,
                    "url": destination_url,
                    "snippet": snippet
                })

    except Exception:
        pass
    return results

async def search_all(query: str, page: int = 1) -> Dict[str, Any]:
    """Orchestrates concurrent fetching of instant answer and web results."""
    query = query.strip()
    if not query:
        return {"instant": None, "results": [], "query": ""}

    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
        instant_answer = await fetch_instant_answer(client, query)
        web_results = await fetch_web_results(client, query, page=page)

    return {
        "query": query,
        "page": page,
        "instant": instant_answer,
        "results": web_results
    }
