def parse_listings(html, meta):
    """Extract listings for a single Mubawab page."""
    soup = BeautifulSoup(html, "html.parser")
    listings = []

    for li in soup.select("li.classifiedItem"):
        # Title
        title_el = li.select_one("h2.listingTit, h2.title, h2")
        title = title_el.get_text(strip=True) if title_el else None

        # Price
        price_el = li.select_one("div.priceTag, span.price, .price")
        price = price_el.get_text(strip=True) if price_el else None

        # Area
        area_el = None
        for span in li.select("div.characteristics span, span, li"):
            if "m²" in span.get_text():
                area_el = span
                break
        area = area_el.get_text(strip=True) if area_el else None

        # Unit
        unit = "m²" if area and "m²" in area else None

        # Link
        link_el = li.select_one("a")
        link = (
            "https://www.mubawab.ma" + link_el["href"]
            if link_el and link_el.get("href")
            else None
        )

        # Image
        img_el = li.select_one("img")
        image = (
            img_el.get("data-src")
            or img_el.get("src")
            if img_el
            else None
        )

        # Location (from title)
        location = None
        if title and " in " in title:
            try:
                location = title.split(" in ", 1)[1].split(".")[0].strip()
            except Exception:
                location = None

        listings.append(
            {
                "title": title,
                "price": price,
                "currency": "DH",
                "area": area,
                "unit": unit,
                "location": location,
                "image": image,
                "link": link,
                "retrieved_at": str(date.today()),
                "price_per_sqm": None,
                "city": meta["city"],
                "listing_type": meta["listing_type"],
                "asset_type": meta["asset_type"],
                "document_name": meta["document_name"],
                "site_name": meta["site_name"],
                "error": None,
            }
        )

    return listings
