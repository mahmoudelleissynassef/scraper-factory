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
MAX_PAGES = 200  # safety cap


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


def parse_price(text: str) -> tuple[Optional[float], Optional[str]]:
    if not text:
        return None, None
    if re.search(r"price\s*on\s*request", text, re.I):
        return None, None

    m = re.search(r"(?P<num>\d[\d\s.,]*)\s*(?P<cur>DH|MAD|DHS|€|\$)", text, re.I)
    if not m:
        return None, None

    num = m.group("num").replace(" ", "").replace(",", "").replace("\u202f", "")
    cur = m.group("cur").upper()
    try:
        value = float(num.replace(".", "")) if num.count(".") > 1 else float(num)
    except ValueError:
        digits = re.sub(r"[^\d.]", "", num)
        try:
            value = float(digits)
        except ValueError:
            return None, cur
    return value, cur


def parse_area(text: str) -> tuple[Optional[float], Optional[str]]:
    if not text:
        return None, None
    m = re.search(r"(?P<num>\d+[\d\s.,]*)\s*(?P<unit>m²|m2|sqm)", text, re.I)
    if not m:
        return None, None

    raw = m.group("num").replace(" ", "").replace(",", "").replace("\u202f", "")
    try:
        val = float(raw)
    except ValueError:
        val = None
    unit = "m²" if m.group("unit").lower() in ("m²", "m2") else "sqm"
    return val, unit


# ---------- Parser ----------
def parse_mubawab(html: str, metadata: dict):
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select("div.listingBox")
    if not cards:
        print("DEBUG: No listings found")
        return []

    items = []
    for box in cards:
        text_all = box.get_text(" ", strip=True)

        # Title
        title = None
        title_tag = box.select_one(".listTitle, .titleRow, p.listingP, .disFlex.titleRow")
        if title_tag:
            title = clean_spaces(title_tag.get_text(" ", strip=True))
        if not title:
            a = box.find("a", href=True)
            if a and len(a.get_text(strip=True)) > 5:
                title = clean_spaces(a.get_text(" ", strip=True))
        if not title:
            title = clean_spaces(text_all[:140])

        # Link
        link = ""
        a_best = box.find("a", href=True)
        if a_best:
            link = a_best.get("href")
        if link and link.startswith("/"):
            link = "https://www.mubawab.ma" + link

        # Price
        price, currency = parse_price(text_all)

        # Area
        area_val, unit = None, None
        area_string = box.find(string=re.compile(r"\d[\d\s.,]*\s*(m²|m2|sqm)", re.I))
        if area_string:
            area_val, unit = parse_area(str(area_string))

        # Image
        image = ""
        img = box.find("img")
        if img:
            image = img.get("data-src") or img.get("src") or ""

        # Price per sqm
        ppsqm = None
        if price and area_val and area_val > 0:
            ppsqm = round(price / area_val, 2)

        items.append({
            "title": title or "",
            "price": price,
            "currency": currency or "",
            "area": area_val,
            "unit": unit or "",
            "location": metadata["city"],
            "image": image or "",
            "link": link or "",
            "retrieved_at": str(date.today()),
            "price_per_sqm": ppsqm,
            "city": metadata["city"],
            "asset_type": metadata["asset_type"],
            "listing_type": metadata["listing_type"],
            "site_name": metadata["site_name"],
            "document_name": metadata["document_name"],
        })

    return items


# ---------- Scraper ----------
async def scrape_pages(base_url: str, metadata: dict, pages: int):
    pages = max(1, min(pages, MAX_PAGES))
    all_items: List[dict] = []

    async with httpx.AsyncClient(headers=UA, timeout=20) as client:
        for page in range(1, pages + 1):
            url = base_url if page == 1 else f"{base_url}:p:{page}"
            print(f"DEBUG Fetching: {url}")

            try:
                resp = await client.get(url)
                resp.raise_for_status()
            except Exception as e:
                print(f"[ERROR] Request failed {url}: {e}")
                break

            html = resp.text
            items = parse_mubawab(html, metadata)

            if not items:
                print(f"DEBUG: No items parsed from {url}")
                break

            all_items.extend(items)

    return all_items


# ---------- API ----------
@app.get("/")
def home():
    return {"message": "Mubawab Scraper running!"}


@app.post("/scrape")
async def scrape(input: ScrapeInput):
    try:
        data = await scrape_pages(input.url, input.dict(), input.pages)
        if not data:
            raise HTTPException(status_code=404, detail="No listings found")
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
