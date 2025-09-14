from fastapi import FastAPI
from pydantic import BaseModel
import requests
from bs4 import BeautifulSoup
from datetime import datetime
import re

app = FastAPI()

# ---- INPUT MODEL ----
class ScrapeInput(BaseModel):
    url: str        # e.g. "https://www.mubawab.ma/en/st/casablanca/office-for-rent"
    city: str       # e.g. "Casablanca"
    asset_type: str # e.g. "Offices"
    site_name: str  # e.g. "Mubawab"

# ---- SCRAPER ----
def scrape_mubawab(url: str, city: str, asset_type: str, site_name: str):
    listings = []
    page = 1
    today = datetime.today().strftime("%Y-%m-%d")

    while True:
        paginated_url = f"{url}?p={page}"
        r = requests.get(paginated_url, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            break

        soup = BeautifulSoup(r.text, "html.parser")
        ads = soup.select("li.listingBox")  # Each listing container
        if not ads:
            break

        for ad in ads:
            try:
                title = ad.select_one(".listingBoxTitle").get_text(strip=True)
            except:
                title = ""

            try:
                link = "https://www.mubawab.ma" + ad.select_one("a")["href"]
            except:
                link = ""

            try:
                image = ad.select_one("img")["src"]
            except:
                image = ""

            # Price
            try:
                raw_price = ad.select_one(".price").get_text(strip=True)
                match = re.match(r"([\d,\.]+)\s*([A-Za-z]+)", raw_price.replace(",", ""))
                if match:
                    price = float(match.group(1))
                    currency = match.group(2)
                else:
                    price, currency = None, None
            except:
                price, currency = None, None

            # Area
            try:
                raw_area = ad.select_one(".property span:contains('m²')").get_text(strip=True)
                match = re.match(r"([\d,\.]+)\s*(m²)", raw_area.replace(",", ""))
                if match:
                    area = float(match.group(1))
                    unit = match.group(2)
                else:
                    area, unit = None, None
            except:
                area, unit = None, None

            # Location (from title text "in {location}.")
            location = ""
            if " in " in title:
                parts = title.split(" in ")
                if len(parts) > 1:
                    location = parts[1].split(".")[0].strip()

            # Price per sqm
            try:
                price_per_sqm = round(price / area, 2) if price and area and area > 0 else None
            except:
                price_per_sqm = None

            listings.append({
                "title": title,
                "price": price,
                "currency": currency,
                "area": area,
                "unit": unit,
                "location": location,
                "image": image,
                "link": link,
                "retrieved_at": today,
                "price_per_sqm": price_per_sqm,
                "city": city,
                "asset_type": asset_type,
                "site_name": site_name
            })

        page += 1

    return listings

# ---- ROUTES ----
@app.get("/")
def home():
    return {"message": "Scraper API is running"}

@app.post("/scrape")
def scrape(data: ScrapeInput):
    if "mubawab.ma" in data.url:
        return scrape_mubawab(data.url, data.city, data.asset_type, data.site_name)
    return {"error": "Unsupported site"}
