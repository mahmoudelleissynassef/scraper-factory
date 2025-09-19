from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from datetime import date
import re
import httpx
from bs4 import BeautifulSoup

app = FastAPI()

# ---------- Config ----------
UA = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}
MAX_PAGES = 200


# ---------- Models ----------
class ScrapeInput(BaseModel):
    url: str
    city: str
    asset_type: str
    site_name: str
    listing_type: str
    document_name: str
    pages: int = 1


# ---------- Helpers ----------
def clean_spaces(t: str) -> str:
    return re.sub(r"\s+", " ", t or "").strip()


# ---------- Parsers ----------
def parse_mubawab(html: str, metadata: dict):
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select("div.listingBox")
    if not cards:
        print("DEBUG: No cards found with div.listingBox")
        return []

    items = []
    for box in cards:
        title = clean_spaces(box.get_text(" ", strip=True))[:120]
        link_tag = box.find("a", href=True)
        link = ""
        if link_tag:
            link = link_tag["href"]
            if link.startswith("/"):
                link = "https://www.mubawab.ma" + link

        items.append({
            "title": title,
            "price": None,
            "currency": "",
            "area": None,
            "unit": "",
            "location": metadata["city"],
            "image": "",
            "link": link,
            "retrieved_at": str(date.today()),
            "price_per_sqm": None,
            "city": metadata["city"],
            "asset_type": metadata["asset_type"],
            "listing_type": metadata["listing_type"],
            "site_name": metadata["site_name"],
            "document_name": metadata["document_name"],
        })
    return items


def parse_coinafrique(html: str, metadata: dict):
    # CoinAfrique loads JSON in <script> tags, so debug dump first
    json_snippet = re.search(r"\{.*\}", html, re.S)
    if not json_snippet:
        print("DEBUG: No JSON snippet found in page")
        return []

    text = json_snippet.group(0)
    print("DEBUG JSON snippet:", text[:1000])  # show first 1000 chars

    # TODO: refine parsing once we confirm structure
    return []


# ---------- Router ----------
def parse_site(html: str, metadata: dict):
    site = metadata.get("site_name", "").lower()
    print(f"DEBUG SITE={site}")
    print("DEBUG HTML snippet:", html[:1000])  # log first 1000 chars

    if "mubawab" in site:
        return parse_mubawab(html, metadata)
    elif "coinafrique" in site:
        return parse_coinafrique(html, metadata)
    else:
        return []


async def scrape_pages(base_url: str, metadata: dict, pages: int):
    pages = max(1, min(pages, MAX_PAGES))
    all_items: List[dict] = []

    async with httpx.AsyncClient(headers=UA, timeout=20) as client:
        for page in range(1, pages + 1):
            if "coinafrique" in metadata["site_name"].lower():
                url = base_url if page == 1 else f"{base_url}&page={page}"
            else:
                url = base_url if page == 1 else f"{base_url}:p:{page}"

            print(f"DEBUG Fetching: {url}")
            try:
                resp = await client.get(url)
                resp.raise_for_status()
            except Exception as e:
                print(f"[ERROR] Request failed {url}: {e}")
                break

            html = resp.text
            items = parse_site(html, metadata)

            if not items:
                print(f"DEBUG: No items parsed from {url}")
                break

            all_items.extend(items)

    return all_items


# ---------- API ----------
@app.get("/")
def home():
    return {"message": "Scraper Factory running!"}


@app.post("/scrape")
async def scrape(input: ScrapeInput):
    try:
        data = await scrape_pages(input.url, input.dict(), input.pages)
        if not data:
            raise HTTPException(status_code=404, detail="No listings found")
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
