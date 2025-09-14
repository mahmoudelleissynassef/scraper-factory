from fastapi import FastAPI
from pydantic import BaseModel
import requests
from bs4 import BeautifulSoup
from datetime import datetime
import re

app = FastAPI(title="Scraper Factory", version="0.1.0")

# ---------- Input model ----------
class ScrapeInput(BaseModel):
    url: str                # listings page URL (e.g., Mubawab city page)
    city: str               # e.g., "Casablanca"
    asset_type: str         # e.g., "Offices"
    site_name: str          # e.g., "Mubawab"

# ---------- Helpers ----------
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0 Safari/537.36"}

def number_from_text(txt: str) -> float | None:
    if not txt:
        return None
    # keep digits, comma, dot, space; then normalize
    cleaned = re.sub(r"[^\d.,\s]", "", txt).strip()
    if not cleaned:
        return None
    # Remove spaces in numbers like "1 200"
    cleaned = cleaned.replace(" ", "")
    # If there are both comma and dot, assume comma is thousands
    if "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(",", "")
    else:
        # If only comma, treat it as decimal separator
        if "," in cleaned:
            cleaned = cleaned.replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None

def parse_price(text: str) -> tuple[float | None, str | None]:
    """Return (price_number, currency). Accepts e.g. '9,000 DH', 'Price on request'."""
    if not text:
        return (None, None)
    if "price on request" in text.lower():
        return (None, None)

    m = re.search(r"([\d\s.,]+)\s*([A-Za-z]{1,4})?", text)
    if not m:
        return (None, None)
    price_val = number_from_text(m.group(1))
    currency = m.group(2) or None
    return (price_val, currency)

def parse_area(text: str) -> tuple[float | None, str | None]:
    """Return (area_number, unit). Accepts '200 m²', '150 m2', etc."""
    if not text:
        return (None, None)
    m = re.search(r"([\d\s.,]+)\s*(m²|m2|sqm)", text, flags=re.I)
    if not m:
        return (None, None)
    area_val = number_from_text(m.group(1))
    unit = m.group(2)
    # normalize unit
    unit = "m²"
    return (area_val, unit)

def location_from_title(title: str) -> str | None:
    """Fallback: pull location from title between 'in ' and first '.'"""
    if not title:
        return None
    m = re.search(r"\bin\s+([^\.]+)", title, flags=re.I)
    return m.group(1).strip() if m else None

def absolute_link(href: str) -> str | None:
    if not href:
        return None
    if href.startswith("http"):
        return href
    return "https://www.mubawab.ma" + href

# ---------- Scraper ----------
def scrape_mubawab_list_page(url: str, city: str, asset_type: str, site_name: str) -> list[dict]:
    res = requests.get(url, headers=UA, timeout=30)
    res.raise_for_status()
    soup = BeautifulSoup(res.text, "html.parser")

    items: list[dict] = []

    # Each listing card
    # From your DevTools: <div class="listingBox sPremium" ...> (sometimes without sPremium)
    for box in soup.select("div.listingBox"):
        # Title
        title_tag = box.select_one(".titleRow")
        title = title_tag.get_text(strip=True) if title_tag else ""

        # Price
        price_tag = box.select_one(".priceBar")
        price_raw = price_tag.get_text(strip=True) if price_tag else ""
        price_val, currency = parse_price(price_raw)

        # Location (prefer the explicit bar; else fallback to title)
        loc_tag = box.select_one(".contactbar")
        location = loc_tag.get_text(strip=True) if loc_tag else location_from_title(title)

        # Area (there may be several 'disFlex flexCol' blocks; the one with m² text is the target)
        area_val = None
        unit = None
        for feat in box.select("div.disFlex.flexCol"):
            txt = feat.get_text(" ", strip=True)
            a, u = parse_area(txt)
            if a:
                area_val, unit = a, u
                break

        # Link (Mubawab often sets linkref on the container, or there is an inner <a>)
        link = box.get("linkref")
        if not link:
            a_tag = box.select_one("a[href]")
            link = a_tag.get("href") if a_tag else None
        link = absolute_link(link) if link else None

        # Image (best-effort)
        img = None
        img_tag = box.find("img")
        if img_tag:
            img = img_tag.get("src") or img_tag.get("data-src") or img_tag.get("data-lazy") or None
            if img and img.startswith("//"):
                img = "https:" + img

        # Price per sqm
        price_per_sqm = None
        if price_val and area_val and area_val > 0:
            price_per_sqm = round(price_val / area_val, 2)

        items.append({
            "title": title or "",
            "price": price_val if price_val is not None else "",
            "currency": currency or "",
            "area": area_val if area_val is not None else "",
            "unit": unit or "",
            "location": location or "",
            "image": img or "",
            "link": link or "",
            "retrieved_at": datetime.today().strftime("%Y-%m-%d"),
            "price_per_sqm": price_per_sqm if price_per_sqm is not None else ""
        })

    return items

# ---------- Routes ----------
@app.get("/")
def home():
    return {"message": "API is running!"}

@app.post("/scrape")
def scrape(input: ScrapeInput):
    """
    Currently supports Mubawab listing pages (server-rendered).
    Required fields in the body:
    {
      "url": "https://www.mubawab.ma/en/st/casablanca/office-for-rent",
      "city": "Casablanca",
      "asset_type": "Offices",
      "site_name": "Mubawab"
    }
    """
    # Basic domain routing (you can extend later for other sites)
    if "mubawab.ma" in input.url:
        return scrape_mubawab_list_page(
            url=input.url,
            city=input.city,
            asset_type=input.asset_type,
            site_name=input.site_name,
        )

    # Unknown site → empty list (or raise a 400 if you prefer)
    return []
