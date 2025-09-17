# main.py
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from datetime import date
import re
import requests
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
MAX_PAGES = 20  # safety cap
DETAIL_IMG_LIMIT = 5
TIMEOUT = 30


# ---------- Models ----------
class ScrapeInput(BaseModel):
    url: str               # listings URL (page 1)
    city: str              # e.g. "Casablanca"
    asset_type: str        # e.g. "Offices"
    site_name: str         # e.g. "Mubawab"
    listing_type: Optional[str] = None  # e.g. "Rent" or "Sale"
    pages: int = 1         # how many result pages to scrape


# ---------- Utilities ----------
def clean_spaces(t: str) -> str:
    return re.sub(r"\s+", " ", t or "").strip()


def parse_price(text: str) -> tuple[Optional[float], Optional[str]]:
    """
    Find number + currency (DH, MAD, DHS, €, $). Handles 'Price on request'.
    Returns (price_float, currency_str).
    """
    if not text:
        return None, None

    if re.search(r"price\s*on\s*request", text, re.I):
        return None, None

    m = re.search(r"(?P<num>\d[\d\s.,]*)\s*(?P<cur>DH|MAD|DHS|€|\$)", text, re.I)
    if not m:
        m = re.search(r"(DH|MAD|DHS|€|\$)\s*(\d[\d\s.,]*)", text, re.I)
        if not m:
            return None, None
        num = m.group(2)
        cur = m.group(1)
    else:
        num = m.group("num")
        cur = m.group("cur")

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
    """
    Extract area value and unit (m²/m2/sqm).
    """
    if not text:
        return None, None

    m = re.search(r"(?P<num>\d+[\d\s.,]*)\s*(?P<unit>m²|m2|sqm)", text, re.I)
    if not m:
        return None, None

    raw = m.group("num")
    unit = m.group("unit")
    raw = raw.replace(" ", "").replace("\u202f", "").replace(",", "")
    try:
        val = float(raw)
    except ValueError:
        val = None
    unit_norm = "m²" if unit.lower() in ("m²", "m2") else "sqm"
    return val, unit_norm


def extract_location_from_title(title: str) -> Optional[str]:
    """
    Heuristic: text after 'in ' and before next '.' or '-'.
    e.g. 'Offices for rent in Hay El Menzah. Area ...' -> 'Hay El Menzah'
    """
    if not title:
        return None
    m = re.search(r"\bin\s+([^.\-]+)", title, re.I)
    if m:
        return clean_spaces(m.group(1))
    return None


def first_attr(tag, *attrs) -> Optional[str]:
    for a in attrs:
        if tag and tag.has_attr(a) and tag[a]:
            return tag[a]
    return None


def pick_first_url_like(src: Optional[str]) -> Optional[str]:
    """For srcset-like strings, pick the first URL."""
    if not src:
        return None
    if "http" in src and " " in src:
        return src.split(",")[0].strip().split(" ")[0]
    return src if src.startswith("http") else None


# ---------- Detail page images ----------
def fetch_detail_images(detail_url: str) -> List[str]:
    """Fetch up to DETAIL_IMG_LIMIT image URLs from a Mubawab detail page."""
    images: List[str] = []
    try:
        r = requests.get(detail_url, headers=UA, timeout=TIMEOUT)
        if r.status_code != 200:
            return images

        soup = BeautifulSoup(r.text, "html.parser")

        # Common galleries: look for many <img> inside containers (swiper, gallery, photo, etc.)
        gallery_selectors = [
            ".swiper", ".gallery", ".photos", ".photo", ".images", ".thumbs", "div"
        ]
        seen = set()

        for sel in gallery_selectors:
            for img in soup.select(f"{sel} img"):
                url = (first_attr(img, "data-src", "src", "data-lazy", "data-original", "srcset") or "").strip()
                url = pick_first_url_like(url)
                if url and url not in seen:
                    images.append(url)
                    seen.add(url)
                    if len(images) >= DETAIL_IMG_LIMIT:
                        return images

        # Fallback: any img on page
        if not images:
            for img in soup.find_all("img"):
                url = (first_attr(img, "data-src", "src", "data-lazy", "data-original", "srcset") or "").strip()
                url = pick_first_url_like(url)
                if url and url not in seen:
                    images.append(url)
                    seen.add(url)
                    if len(images) >= DETAIL_IMG_LIMIT:
                        break

    except Exception:
        pass

    return images


# ---------- List pages ----------
def scrape_mubawab_list_page(url: str, pages: int) -> List[dict]:
    """
    Scrape 1..pages of a Mubawab listings URL.
    """
    pages = max(1, min(pages, MAX_PAGES))
    items: List[dict] = []

    for page in range(1, pages + 1):
        page_url = url if page == 1 else f"{url}:p:{page}"
        resp = requests.get(page_url, headers=UA, timeout=TIMEOUT)
        if resp.status_code != 200:
            break

        soup = BeautifulSoup(resp.text, "html.parser")
        cards = soup.select("div.listingBox")
        if not cards:
            cards = soup.select("div.adlist, div.contentBox, div.box")

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
                area_val, unit = parse_area(area_string if isinstance(area_string, str) else area_string.strip())

            # Image (thumbnail)
            img = box.find("img")
            image = None
            if img:
                image = first_attr(img, "data-src", "src", "data-lazy", "data-original", "srcset")
                image = pick_first_url_like(image)

            # Location
            location = extract_location_from_title(title) or ""

            # price per sqm
            ppsqm = None
            if price and area_val and area_val > 0:
                ppsqm = round(price / area_val, 2)

            # Detail images (up to 5)
            images = fetch_detail_images(link) if link else []

            items.append({
                "title": title or "",
                "price": price if price is not None else None,
                "currency": currency or "",
                "area": area_val if area_val is not None else None,
                "unit": unit or "",
                "location": location,
                "image": image or "",
                "images": images,          # <= NEW: list of up to 5 urls
                "link": link or "",
                "retrieved_at": str(date.today()),
                "price_per_sqm": ppsqm if ppsqm is not None else None,
            })

        if page > 1 and not cards:
            break

    return items


# ---------- API ----------
@app.get("/")
def home():
    return {"message": "API factory is running!"}


@app.post("/scrape")
def scrape(input: ScrapeInput):
    try:
        if "mubawab.ma" in input.url:
            data = scrape_mubawab_list_page(input.url, input.pages)

            # Attach the pass-through fields to each item
            for d in data:
                d["city"] = input.city
                d["asset_type"] = input.asset_type
                d["site_name"] = input.site_name
                d["listing_type"] = input.listing_type or ""

            return data

        raise HTTPException(status_code=400, detail="Unsupported site. Currently supported: Mubawab.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
