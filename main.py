from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from datetime import date
import httpx
from bs4 import BeautifulSoup
import asyncio
import logging
import re

# ---------- Config ----------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

UA = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}
MAX_CONCURRENCY = 5  # don’t overload the site
TIMEOUT = 30.0

app = FastAPI()

# ---------- Models ----------
class ScrapeInput(BaseModel):
    url: str
    city: str
    asset_type: str
    listing_type: str
    site_name: str
    document_name: str
    pages: int = 1


# ---------- Helpers ----------
def extract_number(text: str) -> Optional[float]:
    """Extract clean numeric value (int or float) from a string like '25,000 DH' or '200 m²'."""
    if not text:
        return None
    match = re.search(r"([\d\s,.]+)", text)
    if not match:
        return None
    num = match.group(1)
    num = num.replace(" ", "").replace(",", "")
    try:
        return float(num)
    except ValueError:
        return None


def extract_unit(text: str) -> Optional[str]:
    """Extract the non-numeric unit (e.g. 'DH', 'm²')."""
    if not text:
        return None
    unit = re.sub(r"[\d\s,.]+", "", text)
    return unit.strip() if unit else None


def extract_location_from_title(title: str) -> Optional[str]:
    """Extract location from title: look for 'in X.' pattern."""
    if not title:
        return None
    match = re.search(r"in\s+([^\.]+)", title, re.IGNORECASE)
    return match.group(1).strip() if match else None


def parse_mubawab_listing(card, meta) -> dict:
    """Parse one listing card from Mubawab search results."""
    title_el = card.select_one("h2.section-title, .listing-title, h2")
    title = title_el.get_text(strip=True) if title_el else None

    price_el = card.select_one(".price")
    price_text = price_el.get_text(strip=True) if price_el else None
    price = extract_number(price_text)
    unit = extract_unit(price_text)

    area_el = card.find(string=re.compile(r"m²"))
    area_text = area_el.strip() if area_el else None
    area = extract_number(area_text)
    area_unit = extract_unit(area_text)

    link_el = card.select_one("a")
    link = "https://www.mubawab.ma" + link_el["href"] if link_el and link_el.get("href") else None

    img_el = card.select_one("img")
    image = img_el["src"] if img_el and img_el.get("src") else None

    location = extract_location_from_title(title)

    return {
        "title": title,
        "price": price,
        "unit": unit,
        "area": area,
        "area_unit": area_unit,
        "location": location,
        "image": image,
        "link": link,
        "retrieved_at": str(date.today()),
        **meta,
    }


# ---------- Scraper ----------
async def fetch_page(client: httpx.AsyncClient, url: str, page: int) -> Optional[List[dict]]:
    page_url = f"{url}:p{page}" if page > 1 else url
    logger.debug(f"Fetching {page_url}")
    try:
        resp = await client.get(page_url, headers=UA, timeout=TIMEOUT)
        if resp.status_code == 404:
            logger.warning(f"Page not found: {page_url}")
            return None
        resp.raise_for_status()
    except Exception as e:
        logger.warning(f"Failed to fetch {page_url}: {e}")
        return None

    soup = BeautifulSoup(resp.text, "html.parser")
    cards = soup.select(".listingBox, .property-list, .regular-listing")
    if not cards:
        logger.info(f"No listings found on {page_url}")
        return None

    return cards


async def scrape_site(data: ScrapeInput) -> List[dict]:
    results = []
    meta = {
        "city": data.city,
        "listing_type": data.listing_type,
        "asset_type": data.asset_type,
        "document_name": data.document_name,
        "site_name": data.site_name,
    }

    sem = asyncio.Semaphore(MAX_CONCURRENCY)

    async with httpx.AsyncClient(follow_redirects=True) as client:
        for page in range(1, data.pages + 1):
            async with sem:
                cards = await fetch_page(client, data.url, page)
                if not cards:  # stop gracefully
                    logger.info(f"Stopping early at page {page}")
                    break
                for card in cards:
                    results.append(parse_mubawab_listing(card, meta))

    return results


# ---------- Routes ----------
@app.post("/scrape")
async def scrape(data: ScrapeInput):
    results = await scrape_site(data)
    if not results:
        raise HTTPException(status_code=404, detail="No listings found")
    return results
