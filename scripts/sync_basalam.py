#!/usr/bin/env python3
"""Refresh Nana Accessory products from Basalam's public rendered pages."""
from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "src" / "data" / "products.json"
STORE_URL = "https://basalam.com/nana-accessory"
STORE_PATH = "/nana-accessory"
STORE_NAME = "نانا اکسسوری شاپ"
EXPECTED_VENDOR_ID = 1_161_426
MAX_SCROLLS = 30
PAGE_TIMEOUT_MS = 45_000
HEADERS = {"Accept-Language": "fa-IR,fa;q=0.9,en;q=0.7"}
_DIGIT_MAP = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def ascii_digits(value: str) -> str:
    return value.translate(_DIGIT_MAP)


def product_id_from_href(href: str) -> str | None:
    if not href:
        return None
    parsed = urlparse(href)
    if parsed.netloc and parsed.netloc.lower() not in {"basalam.com", "www.basalam.com"}:
        return None
    match = re.fullmatch(r"/nana-accessory/product/(\d+)/?", parsed.path)
    return match.group(1) if match else None


def walk(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def read_next_data(page) -> dict:
    raw = page.locator("script#__NEXT_DATA__").text_content(timeout=15_000)
    if not raw:
        raise RuntimeError("Basalam page did not expose __NEXT_DATA__.")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Could not parse Basalam __NEXT_DATA__.") from exc


def extract_store_identity(page) -> tuple[int, int | None]:
    next_data = read_next_data(page)
    vendor = next((item for item in walk(next_data)
                   if item.get("identifier") == "nana-accessory" and item.get("title") == STORE_NAME), None)
    if not vendor:
        raise RuntimeError("The page did not identify the expected Nana storefront.")
    vendor_id = int(vendor.get("id", 0))
    if vendor_id != EXPECTED_VENDOR_ID:
        raise RuntimeError(f"Store identity mismatch: expected {EXPECTED_VENDOR_ID}, got {vendor_id}.")
    count = vendor.get("productCount")
    return vendor_id, int(count) if count is not None else None


def collect_product_urls(page, expected_count: int | None) -> list[str]:
    """Scroll until the advertised number is found or the page stops loading."""
    found: set[str] = set()
    stable_rounds = 0
    previous_count = -1
    for scroll_number in range(1, MAX_SCROLLS + 1):
        hrefs = page.locator('a[href*="/nana-accessory/product/"]').evaluate_all(
            "els => els.map(el => el.getAttribute('href')).filter(Boolean)"
        )
        for href in hrefs:
            product_id = product_id_from_href(href)
            if product_id:
                found.add(product_id)

        if expected_count is not None and len(found) >= expected_count:
            break
        if len(found) == previous_count:
            stable_rounds += 1
        else:
            stable_rounds = 0
        if stable_rounds >= 4:
            break
        previous_count = len(found)

        # Scroll by roughly one viewport, then to the bottom on alternating rounds.
        page.evaluate("window.scrollBy(0, Math.max(window.innerHeight * 0.85, 700))")
        page.wait_for_timeout(1_100)
        if scroll_number % 3 == 0:
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(1_400)

    print(f"Found {len(found)} unique Nana product links after scrolling" +
          (f" (store advertises {expected_count})." if expected_count is not None else "."))
    return [f"https://basalam.com{STORE_PATH}/product/{pid}" for pid in sorted(found)]


def extract_jsonld_product(page) -> dict | None:
    """Read Schema.org Product data embedded in the original HTML."""
    return page.evaluate("""() => {
      for (const node of document.querySelectorAll('script[type="application/ld+json"]')) {
        try {
          const parsed = JSON.parse(node.textContent || 'null');
          const queue = Array.isArray(parsed) ? [...parsed] : [parsed];
          while (queue.length) {
            const item = queue.shift();
            if (!item || typeof item !== 'object') continue;
            if (item['@type'] === 'Product' || (Array.isArray(item['@type']) && item['@type'].includes('Product'))) return item;
            if (Array.isArray(item)) queue.push(...item);
            else if (item['@graph']) queue.push(...item['@graph']);
          }
        } catch (_) {}
      }
      return null;
    }""")


def scrape_product(page, url: str) -> dict:
    page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
    try:
        page.locator("h1").first.wait_for(state="visible", timeout=20_000)
        page.locator("script#__NEXT_DATA__").wait_for(state="attached", timeout=15_000)
    except PlaywrightTimeoutError as exc:
        raise RuntimeError(f"Product page did not finish rendering: {url}") from exc

    # Basalam exposes the most reliable product fields in __NEXT_DATA__.
    next_data = read_next_data(page)
    page_props = next_data.get("props", {}).get("pageProps", {})
    product = page_props.get("product") or {}
    vendor = product.get("vendor") or {}
    product_id = product_id_from_href(url)
    if str(product.get("id", "")) != str(product_id):
        raise RuntimeError(f"Product ID mismatch in page data for {url}")
    if int(vendor.get("id", 0)) != EXPECTED_VENDOR_ID or vendor.get("identifier") != "nana-accessory":
        raise RuntimeError(f"Product is not owned by Nana's storefront: {url}")

    title = (product.get("title") or "").strip()
    if not title:
        title = page.locator("h1").first.inner_text().strip()
    if not title:
        raise RuntimeError(f"Could not extract product title from {url}")

    # Basalam's product.price is IRR (rial); the shop displays Toman (rial / 10).
    raw_price = product.get("primaryPrice") or product.get("price")
    price = int(raw_price) // 10 if raw_price is not None else 0

    photo = product.get("photo") or {}
    image = photo.get("lg") or photo.get("original") or photo.get("md") or ""
    if not image:
        photos = product.get("photos") or []
        if photos and isinstance(photos[0], dict):
            image = photos[0].get("lg") or photos[0].get("original") or photos[0].get("md") or ""
    if not image:
        image = page.locator('meta[property="og:image"]').get_attribute("content") or ""

    # Structured data is a fallback for pages whose Next.js product object changes.
    if not price or not image:
        structured = extract_jsonld_product(page) or {}
        offer = structured.get("offers") or {}
        if not price and offer.get("price") is not None:
            raw = int(float(offer["price"]))
            price = raw // 10 if offer.get("priceCurrency") == "IRR" else raw
        if not image:
            images = structured.get("image") or []
            if isinstance(images, str):
                image = images
            elif images:
                first = images[0]
                image = first.get("contentUrl", "") if isinstance(first, dict) else str(first)

    if price <= 0:
        raise RuntimeError(f"Could not extract a valid structured or visible price from {url}")
    if not image.startswith("https://"):
        raise RuntimeError(f"Could not find a valid product image on {url}")

    return {"id": str(product_id), "name": title, "url": url, "image": image, "price": price}


def main() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            locale="fa-IR", extra_http_headers=HEADERS, viewport={"width": 1440, "height": 1000},
        )
        page = context.new_page()
        try:
            page.goto(STORE_URL, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
            page.locator("script#__NEXT_DATA__").wait_for(state="attached", timeout=15_000)
            vendor_id, advertised_count = extract_store_identity(page)
            product_urls = collect_product_urls(page, advertised_count)
            if not product_urls:
                raise RuntimeError("No Nana product-card links were found in the storefront.")
            if advertised_count is not None and len(product_urls) != advertised_count:
                raise RuntimeError(
                    f"Store advertises {advertised_count} products, but {len(product_urls)} links were found. "
                    "Catalogue unchanged; refusing an incomplete scrape."
                )

            products = []
            for index, url in enumerate(product_urls, start=1):
                print(f"Scraping Nana product {index}/{len(product_urls)}: {url}", flush=True)
                products.append(scrape_product(page, url))
                time.sleep(0.2)
        finally:
            browser.close()

    ids = [item["id"] for item in products]
    if len(ids) != len(set(ids)):
        raise RuntimeError("Duplicate product IDs detected; catalogue unchanged.")
    if advertised_count is not None and len(products) != advertised_count:
        raise RuntimeError("Scraped product count differs from store count; catalogue unchanged.")

    payload = {
        "store_url": STORE_URL,
        "vendor_id": vendor_id,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "basalam-public-storefront-html",
        "products": products,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Successfully scraped and saved {len(products)} Nana products.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
