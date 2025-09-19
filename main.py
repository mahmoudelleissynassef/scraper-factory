# main.py
import asyncio
import re
from datetime import date
from typing import List, Optional

import httpx
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

# ---------- FastAPI App ----------
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
CONCURRENCY = 5


# ---------- Models ----------
class ScrapeInput(BaseModel):
    url: str
    city: str
    asset_type: str
    site_name: str
    listing_type: str
    document_name: Optional[str] = None
    pages: int = 9999


# ---------- Utilities ----------
def clean_spaces(t: str) -> str:
    return re.sub(r"\s+", " ", t or "").strip()


def parse_price(text: str) -> tuple[Optional[float], Optional[str]]:
    if not text:
        return None, None
    m = re.search(r"([\d\s.,]+)\s*(CFA|FCFA|DH|MAD|DHS|€|\$)", text, re.I)
    if not m:
        m = re.search(r"(CFA|FCFA|DH|MAD|DHS|€|\$)\s*([\d\s.,]+)", text, re.I)
        if not m:
            return None, None
        num, cur = m.group(2), m.group(1)
    else:
        num, cur = m.group(1), m.group(2)

    num = num.replace(" ", "").replace(",", "").replace("\u202f", "")
    try:
        value = float(num.replace(".", "")) if num.count(".") > 1 else float(num)
    except ValueError:
        digits = re.sub(r"[^\d.]", "", num)
        try:
            value = float(digits)
        except ValueError:
            return None, cur.upper()
    return value, cur.upper()


# ---------- Async HTTP ----------
async def fetch_page(client, url: str) -> Optional[str]:
    try:
        resp = await client.get(url, headers=UA, timeout=15.0)
        if resp.status_code == 200:
            return resp.text
        else:
            print(f"[WARN] {url} returned {resp.status_code}")
            return None
    except Exception as e:
        print(f"[ERROR] Request failed for {url}: {e}")
        return None


# ---------- Mubawab Parser ----------
def parse_mubawab(html: str) -> List[dict]:
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select("div.listingBox")
    if not cards:
        cards = soup.select("div.adlist, div.contentBox, div.box")
    results = []

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
            m = re.search(r"(\d[\d\s.,]*)\s*(m²|m2|sqm)", str(area_string))
            if m:
                try:
                    area_val = float(m.group(1).replace(" ", "").replace(",", ""))
                    unit = "m²"
                except:
                    pass

        # Image
        img = box.find("img")
        image = None
        if img:
            image = img.get("data-src") or img.get("src")

        results.append({
            "title": title or "",
            "price": price,
            "currency": currency or "",
            "area": area_val,
            "unit": unit or "",
            "location": "",
            "image": image or "",
            "link": link or "",
            "retrieved_at": str(date.today()),
            "price_per_sqm": round(price/area_val, 2) if price and area_val else None,
        })
    return results


# ---------- CoinAfrique Detail Parser ----------
async def parse_coinafrique_detail(client, url: str) -> dict:
    html = await fetch_page(client, url)
    if not html:
        return {}

    soup = BeautifulSoup(html, "html.parser")

    # Area
    area_val, unit = None, None
    area_tag = soup.find(string=re.compile(r"(\d+)\s*m2", re.I))
    if area_tag:
        m = re.search(r"(\d+)\s*m2", area_tag)
        if m:
            area_val = float(m.group(1))
            unit = "m²"

    return {"area": area_val, "unit": unit or ""}


# ---------- CoinAfrique Search Parser ----------
async def parse_coinafrique(html: str, client) -> List[dict]:
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select("div.annonce, div.annonce-item")
    results, tasks = [], []

    for box in cards:
        # Title
        title_tag = box.select_one("h2, h3, a")
        title = clean_spaces(title_tag.get_text(strip=True)) if title_tag else ""

        # Link
        a_tag = box.find("a", href=True)
        link = a_tag["href"] if a_tag else ""
        if link.startswith("/"):
            link = "https://ci.coinafrique.com" + link

        # Price
        price_tag = box.select_one(".price")
        price_val, currency = parse_price(price_tag.get_text(strip=True)) if price_tag else (None, "CFA")

        # Location
        loc_tag = box.select_one(".location")
        location = clean_spaces(loc_tag.get_text(strip=True)) if loc_tag else ""

        # Image
        img_tag = box.find("img")
        image = img_tag.get("src") if img_tag else ""

        item = {
            "title": title,
            "price": price_val,
            "currency": currency or "CFA",
            "area": None,
            "unit": "",
            "location": location,
            "image": image,
            "link": link,
            "retrieved_at": str(date.today()),
            "price_per_sqm": None,
        }
        results.append(item)

        if link:
            tasks.append(parse_coinafrique_detail(client, link))

    # Fetch details
    details = await asyncio.gather(*tasks, return_exceptions=True)
    for item, detail in zip(results, details):
        if isinstance(detail, dict):
            item.update(detail)
            if item.get("price") and item.get("area"):
                item["price_per_sqm"] = round(item["price"] / item["area"], 2)

    return results


# ---------- Async Scraper ----------
async def scrape_pages(base_url: str, pages: int, site: str) -> List[dict]:
    pages = max(1, min(pages, MAX_PAGES))
    tasks, results = [], []

    limits = httpx.Limits(max_connections=CONCURRENCY)
    async with httpx.AsyncClient(limits=limits) as client:
        for page in range(1, pages + 1):
            url = base_url if page == 1 else f"{base_url}&page={page}"
            tasks.append(fetch_page(client, url))

        responses = await asyncio.gather(*tasks, return_exceptions=True)

        for i, resp in enumerate(responses, start=1):
            if isinstance(resp, Exception) or not resp:
                print(f"[WARN] Skipping page {i}")
                continue

            if site == "mubawab":
                listings = parse_mubawab(resp)
            elif site == "coinafrique":
                listings = await parse_coinafrique(resp, client)
            else:
                listings = []

            if not listings:
                break
            results.extend(listings)

    return results


# ---------- API ----------
@app.get("/")
def home():
    return {"message": "Scraper API is running!"}


@app.post("/scrape")
async def scrape(input: ScrapeInput):
    try:
        if "mubawab.ma" in input.url:
            data = await scrape_pages(input.url, input.pages, "mubawab")
        elif "coinafrique.com" in input.url:
            data = await scrape_pages(input.url, input.pages, "coinafrique")
        else:
            raise HTTPException(status_code=400, detail="Unsupported site")

        # Attach metadata
        for item in data:
            item["city"] = input.city
            item["asset_type"] = input.asset_type
            item["site_name"] = input.site_name
            item["listing_type"] = input.listing_type
            item["document_name"] = input.document_name or ""

        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
