from fastapi import FastAPI
from pydantic import BaseModel
from typing import List, Optional
import requests
from bs4 import BeautifulSoup
from datetime import date
import re

app = FastAPI()

# Input model
class ScrapeInput(BaseModel):
    url: str
    city: str
    asset_type: str
    site_name: str

# Output model
class Listing(BaseModel):
    title: str
    price: Optional[float]
    currency: Optional[str]
    area: Optional[float]
    unit: Optional[str]
    location: Optional[str]
    image: Optional[str]
    link: str
    retrieved_at: str
    price_per_sqm: Optional[float]

def parse_price(price_text: str):
    if not price_text or "request" in price_text.lower():
        return None, None
    match = re.match(r"([\d.,]+)\s*([A-Za-z]+)", price_text.replace(",", ""))
    if match:
        value = float(match.group(1))
        currency = match.group(2)
        return value, currency
    return None, None

def parse_area(title: str):
    match = re.search(r"(\d+)\s*(m²|sqm|m2)", title)
    if match:
        return float(match.group(1)), match.group(2)
    return None, None

def parse_location(title: str):
    if " in " in title:
        after_in = title.split(" in ", 1)[1]
        location = after_in.split(".")[0].strip()
        return location
    return None

@app.post("/scrape", response_model=List[Listing])
def scrape(input_data: ScrapeInput):
    url = input_data.url
    city = input_data.city
    asset_type = input_data.asset_type
    site_name = input_data.site_name

    listings = []
    page = 1
    while True:
        paged_url = f"{url}:o:{page}"
        res = requests.get(paged_url, headers={"User-Agent": "Mozilla/5.0"})
        if res.status_code != 200:
            break

        soup = BeautifulSoup(res.text, "html.parser")
        cards = soup.select("div.property-list div.property")
        if not cards:
            break

        for card in cards:
            title = card.select_one("h2").get_text(strip=True) if card.select_one("h2") else ""
            price_text = card.select_one(".price").get_text(strip=True) if card.select_one(".price") else None
            image = card.select_one("img")["src"] if card.select_one("img") else None
            link = "https://www.mubawab.ma" + card.select_one("a")["href"] if card.select_one("a") else None

            price, currency = parse_price(price_text) if price_text else (None, None)
            area, unit = parse_area(title)
            location = parse_location(title)

            # Compute price per sqm if both values exist
            price_per_sqm = None
            if price and area and area > 10 and price > 100:  # filter obvious junk
                price_per_sqm = round(price / area, 2)

            listings.append({
                "title": title,
                "price": price,
                "currency": currency,
                "area": area,
                "unit": unit,
                "location": location,
                "image": image,
                "link": link,
                "retrieved_at": str(date.today()),
                "price_per_sqm": price_per_sqm
            })

        page += 1

    return listings
