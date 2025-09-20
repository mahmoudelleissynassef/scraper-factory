from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Optional
import httpx
from bs4 import BeautifulSoup
import asyncio
import re
from datetime import date

app = FastAPI()

# -------- Config --------
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}
MAX_PAGES = 200
CONCURRENT_REQUESTS = 10


class ScrapeInput(BaseModel):
    url: str
    city: str
    asset_type: str
    listing_type: str
    site_name: str
    document_name: str
    pages: Optional[int] = 1


async def fetch_page(client: httpx.AsyncClient, url: str, page: int) -> Optional[str]:
    """Fetch a single page asynchronously, return HTML or None if error."""
    page_url = f"{url}?page={page}" if page > 1 else url   # FIXED pagination
    try:
        resp = await client.get(page_url, headers=HEADERS, timeout=20.0)
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        print(f"[WARN] Skipping {page_url} due to {e}")
        return None


def parse_mubawab(html: str, base: ScrapeInput) -> List[dict]:
    """Parse Mubawab HTML and return list of listings."""
    soup = BeautifulSoup(html, "html.parser")
    listings = []

    for item in soup.select(".listingBox"):
        try:
            title = item.select_one(".listingTit").get_text(strip=True) if item.select_one(".listingTit") else None
            price = item.select_one(".price").get_text(strip=True) if item.select_one(".price") else None
            area = item.select_one(".caracteristique .surface").get_text(strip=True) if item.select_one(".caracteristique .surface") else None

            # Extract location from title
            location = None
            if title:
                match = re.search(r"in (.*?)\.", title)
                if match:
                    location = match.group(1).strip()

            link = item.select_one("a")["href"] if item.select_one("a") else None

            # FIXED: handle img src OR data-src
            image = None
            img_tag = item.select_one("img")
            if img_tag:
                image = img_tag.get("src") or img_tag.get("data-src")

            listings.append({
                "title": title,
                "price": price,
                "area": area,
                "unit": "m²" if area else None,
                "location": location,
                "image": image,
                "link": link,
                "retrieved_at": str(date.today()),
                "city": base.city,
                "listing_type": base.listing_type,
                "asset_type": base.asset_type,
                "document_name": base.document_name,
                "site_name": base.site_name,
            })
        except Exception as e:
            print(f"[WARN] Failed to parse one item: {e}")
            continue

    return listings


@app.post("/scrape")
async def scrape(input_data: ScrapeInput):
    listings: List[dict] = []

    async with httpx.AsyncClient() as client:
        tasks = []
        max_pages = min(input_data.pages or 1, MAX_PAGES)

        for page in range(1, max_pages + 1):
            tasks.append(fetch_page(client, input_data.url, page))

        sem = asyncio.Semaphore(CONCURRENT_REQUESTS)

        async def sem_task(task):
            async with sem:
                return await task

        results = await asyncio.gather(*[sem_task(t) for t in tasks])

        for html in results:
            if html:
                listings.extend(parse_mubawab(html, input_data))

    if not listings:
        raise HTTPException(status_code=404, detail="No listings found")

    return listings
