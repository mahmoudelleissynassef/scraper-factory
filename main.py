import os
import json
import re
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from fastapi import FastAPI, Body
from pydantic import BaseModel

# ---------- Config ----------
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

# ---------- FastAPI ----------
app = FastAPI()

@app.get("/")
def home():
    return {"ok": True, "message": "Scraper Factory API is running."}

# ---------- Models ----------
class ScrapeInput(BaseModel):
    url: str
    selectors: dict | None = None
    pages: int = 1
    suffix_template: str | None = None

# ---------- Helpers ----------
def _fetch_html(url: str) -> str:
    headers = {"User-Agent": "Mozilla/5.0"}
    r = requests.get(url, headers=headers, timeout=30)
    r.raise_for_status()
    return r.text

def _abs_url(base: str, href: str) -> str:
    return urljoin(base, href) if href else ""

def _extract_text(el):
    return el.get_text(" ", strip=True) if el else ""

def _extract_price(text: str):
    """Split price into value + currency"""
    if not text:
        return "", ""
    m = re.match(r"([\d\s,\.]+)\s*(\w+)", text)
    if m:
        value = m.group(1).replace(" ", "").replace(",", "")
        currency = m.group(2)
        try:
            return float(value), currency
        except:
            return "", currency
    return text, ""

def _extract_area(text: str):
    """Split area into value + unit"""
    if not text:
        return "", ""
    m = re.match(r"([\d\s,\.]+)\s*(m²|sqm|sqft)?", text, re.I)
    if m:
        value = m.group(1).replace(" ", "").replace(",", "")
        unit = m.group(2) or "m²"
        try:
            return float(value), unit
        except:
            return "", unit
    return text, ""

def _extract_location_from_title(title: str):
    m = re.search(r"\bin\s+([^\.]+)\.", title, flags=re.I)
    return m.group(1).strip() if m else ""

def _page_url(base_url: str, page: int, suffix_template: str | None) -> str:
    if page == 1:
        return base_url
    if suffix_template:
        return base_url + suffix_template.replace("{page}", str(page))
    return f"{base_url}:p:{page}"

# ---------- API ----------
@app.post("/scrape")
def scrape(payload: ScrapeInput):
    selectors = payload.selectors or {
        "container": ".listingBox",
        "title": ["h2", ".title"],
        "price": [".price"],
        "area": [".detail", ".area"],
        "location": [".location"],
        "image": ["img"],
        "link": ["a"],
    }

    results = []
    base_parsed = urlparse(payload.url)
    base_origin = f"{base_parsed.scheme}://{base_parsed.netloc}"

    for page in range(1, payload.pages + 1):
        page_url = _page_url(payload.url, page, payload.suffix_template)
        html = _fetch_html(page_url)
        soup = BeautifulSoup(html, "html.parser")
        cards = soup.select(selectors.get("container", ".listingBox"))

        for card in cards:
            title = _extract_text(card.select_one(selectors["title"][0]))
            price_raw = _extract_text(card.select_one(selectors["price"][0]))
            area_raw = _extract_text(card.select_one(selectors["area"][0]))
            location = _extract_text(card.select_one(selectors["location"][0])) or _extract_location_from_title(title)
            img = card.select_one(selectors["image"][0])
            image = img["src"] if img and img.has_attr("src") else ""
            a = card.select_one(selectors["link"][0])
            link = _abs_url(base_origin, a["href"]) if a and a.has_attr("href") else ""

            price_val, price_cur = _extract_price(price_raw)
            area_val, area_unit = _extract_area(area_raw)

            price_per_m2 = ""
            if isinstance(price_val, float) and isinstance(area_val, float) and area_val > 0:
                price_per_m2 = price_val / area_val

            results.append({
                "title": title,
                "price_value": price_val,
                "price_currency": price_cur,
                "area_value": area_val,
                "area_unit": area_unit,
                "price_per_m2": price_per_m2,
                "location": location,
                "image": image,
                "link": link,
            })

    return {"count": len(results), "items": results}
