# main.py
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from datetime import date
import re
import asyncio
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
CONCURRENT_REQUESTS = 8   # don’t overload site
REQUEST_TIMEOUT = 20       # seconds


# ---------- Models ----------
class ScrapeInput(BaseModel):
    url: str
    city: str
    asset_type: str
    site_name: str
    listing_type: Optional[str] = None
    pages: int = 9999


# ---------- Utilities ----------
def clean_spaces(t: str) -> str:
    return re.sub(r"\s+", " ", t or "").strip()


def parse_price(text: str) -> tuple[Optional[float], Optional[str]]:
    if not text:
        return None, None
    if re.search(r"price\s*on\s*request", text, re.I):
        return None, None

    m = re.search(r"(?P<num>\d[\d\s.,]*)\s*(?P<cur>DH|MAD|DHS|€|\$)", text, re.I)
    if not m:
        m = re.search(r"(DH|MAD|DHS|€|\$)\s*(\d[\d\s.,]*)", text, re.I)
        if not m:
            return None, None
        num, cur = m.group(2), m.group(1)
    else:
        num, cur = m.group("num"), m.group("cur")

    num = num.replace(" ", "").replace(",", "").replace("\u202f", "")
    try:
        value = float(num.replace(".", "")) if num.count(".") > 1 else float(num.replace(",", ""))
    except ValueError:
        digits = re.sub(r"[^\d.]", "", num)
        try:
            value = float(digits)
        except ValueError:
            return None, cur.upper()
    return value, cur.upper()


def parse_area(text: str) -> tuple[Optional[float], Optional[str]]:
    if not text:
        return None, None
    m = re.search(r"(?P<num>\d+[\d\s.,]*)\s*(?P<unit>m²|m2|sqm)", text, re.I)
    if not m:
        return None, None
    raw, unit = m.group("num"), m.group("unit")
    raw = raw.replace(" ", "").replace("\u202f", "").replace(",", "")
    try:
        val = float(raw)
    except ValueError:
        val = None
    unit_norm = "m²" if unit.lower() in ("m²", "m2") else "sqm"
    return val, unit_norm


def extract_location_from_title(title: str) -> Optional[str]:
    if not title:
        return None
    m = re.search(r"\bin\s+([^.\-]+)", title, re.I)
    return clean_spaces(m.group(1)) if m else None


def first_attr(tag, *attrs) -> Optional[str]:
    for a in attrs:
        if tag and tag.has_attr(a) and tag[a]:
            return tag[a]
    return None


# ---------- Async Scraper ----------
async def scrape_mubawab_page(client: httpx.AsyncClient, url: str, page: int) -> List[dict]:
    page_url = url if page == 1 else f"{url}:p:{page}"
    try:
        resp = await client.get(page_url, headers=UA, timeout=REQUEST_TIMEOUT)
    except Exception as e:
        print(f"[ERROR] Failed request {page_url}: {e}")
        return []

    if resp.status_code != 200:
        print(f"[WARN] Status {resp.status_code} at {page_url}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    cards = soup.select("div.listingBox") or soup.select("div.adlist, div.contentBox, div.box")
    if not cards:
        print(f"[INFO] No listings found on {page_url}")
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
        link = None
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
        img = box.find("img")
        image = None
        if img:
            image = first_attr(img, "data-src", "src", "data-lazy", "data-original", "srcset")
            if image and " " in image and "http" in image:
                image = image.split(",")[0].strip().split(" ")[0]

        # Location
        location = extract_location_from_title(title) or ""

        # Price per sqm
        ppsqm = round(price / area_val, 2) if price and area_val and area_val > 0 else None

        items.append({
            "title": title or "",
            "price": price,
            "currency": currency or "",
            "area": area_val,
            "unit": unit or "",
            "location": location,
            "image": image or "",
            "link": link or "",
            "retrieved_at": str(date.today()),
            "price_per_sqm": ppsqm,
        })

    print(f"[INFO] Scraped {len(cards)} listings from {page_url}")
    return items


async def scrape_mubawab_list_page(url: str, pages: int) -> List[dict]:
    pages = max(1, min(pages, MAX_PAGES))
    sem = asyncio.Semaphore(CONCURRENT_REQUESTS)

    async with httpx.AsyncClient() as client:
        async def bounded_scrape(p):
            async with sem:
                try:
                    return await scrape_mubawab_page(client, url, p)
                except Exception as e:
                    print(f"[ERROR] Page {p} failed: {e}")
                    return []

        tasks = [bounded_scrape(page) for page in range(1, pages + 1)]
        results = await asyncio.gather(*tasks)

    # flatten
    return [item for sublist in results for item in sublist]


# ---------- API ----------
@app.get("/")
def home():
    return {"message": "Async Scraper API is running!"}


@app.post("/scrape")
async def scrape(input: ScrapeInput):
    try:
        if "mubawab.ma" in input.url:
            data = await scrape_mubawab_list_page(input.url, input.pages)
            return {
                "city": input.city,
                "asset_type": input.asset_type,
                "site_name": input.site_name,
                "listing_type": input.listing_type,
                "retrieved_at": str(date.today()),
                "count": len(data),
                "listings": data,
            }
        raise HTTPException(status_code=400, detail="Unsupported site. Currently only: Mubawab.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
