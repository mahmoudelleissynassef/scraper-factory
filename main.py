from fastapi import FastAPI
from pydantic import BaseModel
import requests
from bs4 import BeautifulSoup
from datetime import date

app = FastAPI()

# ----------- INPUT MODELS -----------
class ScrapeInput(BaseModel):
    url: str
    city: str
    asset_type: str
    site_name: str


# ----------- SCRAPER LOGIC -----------
def scrape_mubawab(url: str, city: str, asset_type: str, site_name: str):
    listings_data = []
    page = 1

    while True:
        page_url = f"{url}:p:{page}"
        resp = requests.get(page_url)
        if resp.status_code != 200:
            break

        soup = BeautifulSoup(resp.content, "html.parser")
        listings = soup.find_all("li", class_="listingBox")
        if not listings:
            break

        for listing in listings:
            title = listing.find("h2").get_text(strip=True) if listing.find("h2") else ""
            price = listing.find("div", class_="price").get_text(strip=True) if listing.find("div", class_="price") else ""
            area = ""
            for span in listing.find_all("span"):
                if "m²" in span.get_text():
                    area = span.get_text(strip=True)
                    break
            location = ""
            if " in " in title:
                location = title.split(" in ")[-1].split(".")[0].strip()
            link = listing.find("a")["href"] if listing.find("a") else ""
            image = listing.find("img")["src"] if listing.find("img") else ""

            listings_data.append({
                "title": title,
                "price": price,
                "area": area,
                "location": location,
                "link": link,
                "image": image,
                "city": city,
                "asset_type": asset_type,
                "site": site_name,
                "retrieved_at": str(date.today())
            })

        page += 1

    return listings_data


# ----------- ENDPOINTS -----------
@app.get("/")
def home():
    return {"message": "API Factory is running!"}


@app.post("/scrape")
def scrape(input: ScrapeInput):
    if "mubawab.ma" in input.url:
        return scrape_mubawab(input.url, input.city, input.asset_type, input.site_name)
    else:
        return {"error": "Scraper not implemented for this site yet"}
