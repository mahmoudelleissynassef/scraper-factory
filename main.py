from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from datetime import date
from urllib.parse import urlparse, urljoin
import re
import httpx
from bs4 import BeautifulSoup

app = FastAPI(title="Ghana Listings Scraper")

# ---------- Config ----------
UA = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}
MAX_PAGES = 200
TIMEOUT = 20.0
CONCURRENCY = 5


# ---------- Models ----------
class ScrapeInput(BaseModel):
    url: str                 # listing/search URL (page 1)
    city: str
    asset_type: str
    site_name: str           # e.g., "Meqasa" / "Tonaton" / "Jiji"
    listing_type: str        # e.g., "Rent" / "Sale"
    document_name: str
    pages: int = 1           # how many listing pages to fetch (safeguarded by MAX_PAGES)


# ---------- Utilities ----------
def clean(t: Optional[str]) -> str:
    return re.sub(r"\s+", " ", t or "").strip()


def parse_price(text: str) -> tuple[Optional[float], Optional[str]]:
    """
    Extract price and currency from text.
    Supports: GHS, GH₵, ₵, CFA, $, €, £, ₦, MAD (just in case).
    """
    if not text:
        return None, None

    if re.search(r"price\s*on\s*request|call\s*for\s*price", text, re.I):
        return None, None

    # try "<num> <cur>"
    m = re.search(
        r"(?P<num>\d[\d\s,\.]*)\s*(?P<cur>GHS|GH₵|₵|CFA|USD|\$|€|£|₦|MAD|DH|DHS)",
        text, re.I,
    )
    if not m:
        # try "<cur> <num>"
        m = re.search(
            r"(?P<cur>GHS|GH₵|₵|CFA|USD|\$|€|£|₦|MAD|DH|DHS)\s*(?P<num>\d[\d\s,\.]*)",
            text, re.I,
        )
        if not m:
            return None, None

    num = m.group("num")
    cur = m.group("cur").upper()

    # normalize number
    num = num.replace(" ", "").replace("\u202f", "")
    # if number has many separators, fall back to stripping non-digits except dot
    try:
        value = float(num.replace(",", "")) if num.count(",") else float(num)
    except ValueError:
        digits = re.sub(r"[^\d\.]", "", num)
        try:
            value = float(digits)
        except ValueError:
            return None, cur

    # normalize currency symbols
    if cur in {"$", "USD"}:
        cur = "USD"
    if cur in {"GH₵", "₵"}:
        cur = "GHS"
    if cur in {"DH", "DHS"}:
        cur = "MAD"

    return value, cur


def parse_area_any(text: str) -> tuple[Optional[float], Optional[str]]:
    """
    Extract area values from arbitrary text.
    Supports m²/m2/sqm/sq m and ft²/ft2/sqft/sq ft.
    Converts ft² -> m².
    """
    if not text:
        return None, None

    # metric first
    m = re.search(r"(\d[\d\s,\.]*)\s*(m²|m2|sqm|sq\s*m)", text, re.I)
    if m:
        raw = m.group(1).replace(" ", "").replace("\u202f", "")
        try:
            val = float(raw.replace(",", "")) if raw.count(",") else float(raw)
        except ValueError:
            val = None
        return val, "m²" if val is not None else None

    # imperial
    m = re.search(r"(\d[\d\s,\.]*)\s*(ft²|ft2|sqft|sq\s*ft)", text, re.I)
    if m:
        raw = m.group(1).replace(" ", "").replace("\u202f", "")
        try:
            ft2 = float(raw.replace(",", "")) if raw.count(",") else float(raw)
            val = round(ft2 / 10.7639, 2)  # convert to m²
        except ValueError:
            val = None
        return val, "m²" if val is not None else None

    return None, None


def price_per_sqm(price: Optional[float], area: Optional[float]) -> Optional[float]:
    if price and area and area > 0:
        return round(price / area, 2)
    return None


def pick_img(tag) -> str:
    if not tag:
        return ""
    for a in ("data-src", "data-lazy", "data-original", "src", "srcset"):
        v = tag.get(a)
        if v:
            if " " in v and "http" in v:
                # srcset first candidate
                v = v.split(",")[0].strip().split(" ")[0]
            return v
    return ""


# ---------- Generic Card Parser ----------
def parse_generic_cards(html: str, base_url: str) -> List[dict]:
    """
    Heuristic parser that works for many listing sites:
    - looks for common card containers and pulls fields via common class names + regex
    - resolves relative links
    """
    soup = BeautifulSoup(html, "html.parser")

    # plausible card selectors (cover meqasa / tonaton / jiji gh / generic themes)
    candidates = soup.select(
        "article, li, div"
        ".listing-card, "
        ".property-card, "
        ".property, "
        ".card, "
        ".mqs-prop-unit, "
        ".grid-item, "
        ".aditem, "
        ".list-item, "
        ".result-item"
    )
    # if the gigantic selector returned nothing, fallback to broader scan
    if not candidates:
        candidates = soup.find_all(["article", "li", "div"], class_=re.compile(r"(listing|property|card|result|item)", re.I))

    items: List[dict] = []
    for box in candidates:
        # title
        title_tag = box.select_one("h1, h2, h3, .title, .property-title, .listing-title")
        title = clean(title_tag.get_text(" ", strip=True) if title_tag else box.get_text(" ", strip=True)[:140])

        # price (prefer price-ish classes, else regex over box text)
        price_text = ""
        ptag = box.select_one(".price, .property-price, .listing-price, [class*='price']")
        if ptag:
            price_text = ptag.get_text(" ", strip=True)
        else:
            price_text = box.get_text(" ", strip=True)
        price_val, currency = parse_price(price_text)

        # area
        area_val, unit = parse_area_any(box.get_text(" ", strip=True))

        # location
        loc_tag = box.select_one(".location, .address, .property-location, .loc, [class*='location']")
        location = clean(loc_tag.get_text(" ", strip=True)) if loc_tag else ""

        # link
        link = ""
        a = box.find("a", href=True)
        if a:
            link = a["href"]
            if link.startswith("/"):
                parsed = urlparse(base_url)
                link = urljoin(f"{parsed.scheme}://{parsed.netloc}", link)

        # image
        image = ""
        img = box.find("img")
        if img:
            image = pick_img(img)

        # skip garbage
        if not link and not title:
            continue

        items.append({
            "title": title or "",
            "price": price_val,
            "currency": currency or "",
            "area": area_val,
            "unit": unit or "",
            "location": location,
            "image": image or "",
            "link": link or "",
            "retrieved_at": str(date.today()),
            "price_per_sqm": price_per_sqm(price_val, area_val),
        })

    return items


# ---------- Async HTTP ----------
async def fetch(client: httpx.AsyncClient, url: str) -> Optional[str]:
    try:
        r = await client.get(url, headers=UA, timeout=TIMEOUT)
        if r.status_code == 200:
            return r.text
        print(f"[WARN] {url} -> {r.status_code}")
    except Exception as e:
        print(f"[ERROR] {url} -> {e}")
    return None


def build_page_url(base_url: str, page: int) -> str:
    """
    Try common Ghana sites’ pagination styles:
    - ?page=N
    - &page=N
    - /page/N
    Falls back to base_url for page 1.
    """
    if page == 1:
        return base_url

    if "?" in base_url:
        return f"{base_url}&page={page}"
    return f"{base_url.rstrip('/')}/page/{page}"


# ---------- Scraper ----------
async def scrape_pages(base_url: str, pages: int) -> List[dict]:
    pages = max(1, min(pages, MAX_PAGES))
    results: List[dict] = []
    limits = httpx.Limits(max_connections=CONCURRENCY)

    async with httpx.AsyncClient(limits=limits, follow_redirects=True) as client:
        for p in range(1, pages + 1):
            url = build_page_url(base_url, p)
            html = await fetch(client, url)
            if not html:
                break
            items = parse_generic_cards(html, base_url)
            if not items:
                # stop when no more items
                break
            results.extend(items)

    return results


# ---------- API ----------
@app.get("/")
def home():
    return {"ok": True, "message": "Ghana listings scraper is live."}


@app.post("/scrape")
async def scrape(input: ScrapeInput):
    try:
        listings = await scrape_pages(input.url, input.pages)
        if not listings:
            raise HTTPException(status_code=404, detail="No listings found")

        # attach metadata
        for row in listings:
            row["city"] = input.city
            row["asset_type"] = input.asset_type
            row["site_name"] = input.site_name
            row["listing_type"] = input.listing_type
            row["document_name"] = input.document_name

        return listings
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
