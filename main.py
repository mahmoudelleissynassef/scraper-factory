from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from bs4 import BeautifulSoup
import httpx
import asyncio
import re
import json

app = FastAPI()

# ---------- Config ----------
UA = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X X; rv:109.0) "
        "Gecko/20100101 Firefox/117.0"
    )
}
MAX_PAGES = 200
CONCURRENCY_LIMIT = 5
TIMEOUT = 30.0

# ---------- Models ----------
class ScrapeInput(BaseModel):
    url: str
    city: str
    asset_type: str
    site_name: str
    listing_type: str
    document_name: str
    pages: Optional[int] = 1


# ---------- Parsers ----------
def parse_mubawab(html: str, metadata: dict):
    """Parse Mubawab listing cards"""
    soup = BeautifulSoup(html, "html.parser")
    listings = []

    for card in soup.select("li[class*='listingBox']"):
        title = card.select_one("h2, h3")
        price = card.select_one(".price")
        area = card.select_one(".specific-price")

        price_val, currency = None, None
        if price:
            txt = price.get_text(strip=True)
            match = re.search(r"([\d\s,.]+)", txt)
            if match:
                price_val = re.sub(r"[^\d]", "", match.group(1))
            if "MAD" in txt:
                currency = "MAD"
            elif "USD" in txt:
                currency = "USD"

        area_val = None
        if area:
            m2 = re.search(r"([\d\s,.]+)\s*m", area.get_text(strip=True))
            if m2:
                area_val = re.sub(r"[^\d]", "", m2.group(1))

        price_per_sqm = None
        if price_val and area_val:
            try:
                price_per_sqm = float(price_val) / float(area_val)
            except Exception:
                pass

        listings.append({
            "title": title.get_text(strip=True) if title else None,
            "price": price_val,
            "currency": currency,
            "area": area_val,
            "unit": "m²" if area_val else None,
            "image": card.select_one("img")["src"] if card.select_one("img") else None,
            "link": card.select_one("a")["href"] if card.select_one("a") else None,
            "price_per_sqm": price_per_sqm,
            **metadata,
        })

    return listings


def parse_coinafrique(html: str, metadata: dict):
    """Parse CoinAfrique JSON embedded in page"""
    soup = BeautifulSoup(html, "html.parser")
    listings = []

    # Look for a <script> with JSON that contains "latitude" and "prix"
    script = soup.find("script", string=re.compile("prix"))
    if not script:
        return listings

    # Try to extract JSON object from inside
    match = re.search(r"(\{.*\"prix\".*\})", script.string or "", re.DOTALL)
    if not match:
        return listings

    try:
        data = json.loads(match.group(1))
    except Exception:
        return listings

    # Debug: log keys so we can refine
    print("DEBUG CoinAfrique JSON keys:", list(data.keys()))

    ads = data.get("ads") or data.get("listings") or []
    for ad in ads:
        price = ad.get("prix")
        area = ad.get("superficie") or None
        currency = "CFA" if price else None

        price_per_sqm = None
        if price and area:
            try:
                price_per_sqm = float(price) / float(area)
            except Exception:
                pass

        listings.append({
            "title": ad.get("titre") or ad.get("nom"),
            "price": price,
            "currency": currency,
            "area": area,
            "unit": "m²" if area else None,
            "location": ad.get("ville") or ad.get("localisation"),
            "image": ad.get("image") or None,
            "link": ad.get("url") or None,
            "price_per_sqm": price_per_sqm,
            **metadata,
        })

    return listings


# ---------- Dispatcher ----------
def parse_site(html: str, metadata: dict):
    site = metadata.get("site_name", "").lower()
    if "mubawab" in site:
        return parse_mubawab(html, metadata)
    elif "coinafrique" in site:
        return parse_coinafrique(html, metadata)
    else:
        return []


# ---------- Scraper ----------
async def fetch_page(client, url: str, sem, page: int, base: ScrapeInput):
    page_url = url
    if "mubawab" in base.site_name.lower():
        if page > 1:
            page_url = re.sub(r"/en/st/([^/]+)", f"/en/st/\\1:p:{page}", url)

    async with sem:
        try:
            resp = await client.get(page_url, headers=UA, timeout=TIMEOUT)
            resp.raise_for_status()
            return parse_site(resp.text, base.dict())
        except Exception as e:
            print(f"[ERROR] {page_url} -> {e}")
            return []


# ---------- Endpoint ----------
@app.post("/scrape")
async def scrape(input: ScrapeInput):
    pages = min(input.pages or 1, MAX_PAGES)
    sem = asyncio.Semaphore(CONCURRENCY_LIMIT)

    async with httpx.AsyncClient(follow_redirects=True) as client:
        tasks = [fetch_page(client, input.url, sem, p, input) for p in range(1, pages + 1)]
        results = await asyncio.gather(*tasks)

    all_listings = [item for sub in results for item in sub]

    if not all_listings:
        raise HTTPException(status_code=404, detail="No listings found")

    return {"count": len(all_listings), "listings": all_listings}
