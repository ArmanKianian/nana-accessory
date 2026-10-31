#!/usr/bin/env python3
"""Scrape Nana Accessory's public Basalam storefront and its product pages.

No guessed API endpoints and no site-wide link crawling: product URLs must be
under /nana-accessory/product/<numeric-id> on basalam.com. Each product page is
opened and its visible title, price and image are extracted from rendered HTML.
"""
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
EXPECTED_VENDOR_ID = 1_161_426  # Read from the store page's embedded vendor data.
MAX_SCROLLS = 12
PAGE_TIMEOUT_MS = 45_000
HEADERS = {
    "Accept-Language": "fa-IR,fa;q=0.9,en;q=0.7",
}

_DIGIT_MAP = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def ascii_digits(value: str) -> str:
    return value.translate(_DIGIT_MAP)


def product_id_from_href(href: str) -> str | None:
    """Accept only product links belonging to Nana's exact storefront path."""
    if not href:
        return None
    parsed = urlparse(href)
    if parsed.netloc and parsed.netloc.lower() not in {"basalam.com", "www.basalam.com"}:
        return None
    match = re.fullmatch(r"/nana-accessory/product/(\d+)/?", parsed.path)
    return match.group(1) if match else None


def parse_count(text: str) -> int | None:
    normalized = ascii_digits(text)
    match = re.search(r"([0-9][0-9,٬،]*)\s*محصول\s*موجود", normalized)
    if not match:
        return None
    return int(re.sub(r"\D", "", match.group(1)))


def parse_visible_price(lines: list[str]) -> int | None:
    """Read a standalone price line, not stock counts or unrelated page numbers."""
    for line in lines:
        value = ascii_digits(line).strip().replace("٬", "").replace("،", "").replace(",", "").replace(" ", "")
        if re.fullmatch(r"[0-9]{4,}", value):
            amount = int(value)
            # Basalam accessory prices are shown in Toman; reject implausible noise.
            if 1_000 <= amount <= 10_000_000_000:
                return amount
    return None


def extract_store_identity(page) -> tuple[int, int | None]:
    """Verify the rendered store is Nana, using its embedded Next.js state."""
    data = page.locator("script#__NEXT_DATA__").text_content(timeout=10_000)
    if not data:
        raise RuntimeError("Nana storefront did not expose its embedded store data.")
    try:
        next_data = json.loads(data)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Could not parse Basalam storefront data.") from exc

    def walk(value: Any):
        if isinstance(value, dict):
            yield value
            for child in value.values():
                yield from walk(child)
        elif isinstance(value, list):
            for child in value:
                yield from walk(child)

    vendor = next((item for item in walk(next_data)
                   if item.get("identifier") == "nana-accessory" and item.get("title") == STORE_NAME), None)
    if not vendor:
        raise RuntimeError("The page did not identify the expected Nana Accessory storefront.")
    vendor_id = int(vendor.get("id", 0))
    if vendor_id != EXPECTED_VENDOR_ID:
        raise RuntimeError(f"Store identity mismatch: expected {EXPECTED_VENDOR_ID}, got {vendor_id}.")
    count = vendor.get("productCount")
    return vendor_id, int(count) if count is not None else None


def collect_product_urls(page) -> list[str]:
    """Collect only exact Nana product-card URLs after triggering lazy loading."""
    found: set[str] = set()
    for _ in range(MAX_SCROLLS):
        hrefs = page.locator('a[href*="/nana-accessory/product/"]').evaluate_all(
            "els => els.map(el => el.getAttribute('href')).filter(Boolean)"
        )
        for href in hrefs:
            product_id = product_id_from_href(href)
            if product_id:
                found.add(product_id)
        if len(found) >= 18:
            break
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(900)

    return [f"https://basalam.com{STORE_PATH}/product/{pid}" for pid in sorted(found)]


def scrape_product(page, url: str) -> dict:
    page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
    try:
        page.locator("h1").first.wait_for(state="visible", timeout=15_000)
    except PlaywrightTimeoutError as exc:
        raise RuntimeError(f"Product page did not render an H1: {url}") from exc
    page.wait_for_timeout(500)

    data = page.evaluate("""() => {
      const text = document.body?.innerText || '';
      const h1 = document.querySelector('h1')?.innerText?.trim() || '';
      const meta = (key) => document.querySelector(`meta[property='${key}'],meta[name='${key}']`)?.content || '';
      const image = meta('og:image') || meta('twitter:image') || '';
      const canonical = document.querySelector('link[rel=canonical]')?.href || location.href;
      const images = [...document.images].map(i => ({src: i.currentSrc || i.src, alt: i.alt || ''}));
      return {text, h1, image, canonical, images,
        vendorLink: [...document.querySelectorAll('a[href]')].some(a => {
          try { const u = new URL(a.href); return u.hostname.endsWith('basalam.com') && u.pathname.replace(/\\/$/, '') === '/nana-accessory'; }
          catch { return false; }
        })};
    }""")

    if STORE_NAME not in data["text"] and not data["vendorLink"]:
        raise RuntimeError(f"Product does not appear to belong to Nana's shop: {url}")
    title = data["h1"]
    if not title or title == STORE_NAME:
        raise RuntimeError(f"Could not extract a product title from {url}")

    lines = [line.strip() for line in data["text"].splitlines() if line.strip()]
    try:
        title_index = next(i for i, line in enumerate(lines) if line == title)
    except StopIteration:
        title_index = 0
    price = parse_visible_price(lines[title_index + 1:title_index + 12])
    if price is None:
        # Fallback to machine-readable product metadata if Basalam adds it.
        price = page.evaluate("""() => {
          const raw = document.querySelector('meta[property="product:price:amount"]')?.content;
          if (!raw) return null;
          const n = Number(raw.replace(/[^0-9.]/g, ''));
          return Number.isFinite(n) && n > 0 ? Math.round(n) : null;
        }""")
    if not price:
        raise RuntimeError(f"Could not find the visible product price on {url}")

    image = data["image"]
    if not image:
        matching = next((item["src"] for item in data["images"]
                         if item["src"] and (title in item["alt"] or item["alt"] in title)), "")
        image = matching or next((item["src"] for item in data["images"]
                                  if item["src"] and "statics.basalam.com" in item["src"]), "")
    if not image.startswith("https://"):
        raise RuntimeError(f"Could not find a valid product image on {url}")

    product_id = product_id_from_href(url)
    if not product_id:
        raise RuntimeError(f"Unexpected product URL: {url}")
    return {"id": product_id, "name": title, "url": url, "image": image, "price": price}


def main() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            locale="fa-IR",
            extra_http_headers=HEADERS,
            viewport={"width": 1440, "height": 1000},
        )
        page = context.new_page()
        page.goto(STORE_URL, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
        page.locator("script#__NEXT_DATA__").wait_for(state="attached", timeout=15_000)
        vendor_id, advertised_count = extract_store_identity(page)
        product_urls = collect_product_urls(page)

        if not product_urls:
            raise RuntimeError("No Nana product-card links were found in the rendered storefront.")
        if advertised_count is not None and len(product_urls) != advertised_count:
            raise RuntimeError(
                f"Storefront advertises {advertised_count} products, but only {len(product_urls)} Nana product links were found. "
                "Catalogue unchanged; refusing an incomplete scrape."
            )

        products = []
        for index, url in enumerate(product_urls, start=1):
            print(f"Scraping Nana product {index}/{len(product_urls)}: {url}")
            products.append(scrape_product(page, url))
            time.sleep(0.25)
        browser.close()

    ids = [item["id"] for item in products]
    if len(ids) != len(set(ids)):
        raise RuntimeError("Duplicate product IDs detected; catalogue unchanged.")
    if advertised_count is not None and len(products) != advertised_count:
        raise RuntimeError("Scraped product count differs from the store count; catalogue unchanged.")

    old_count = 0
    if OUTPUT.exists():
        try:
            old_data = json.loads(OUTPUT.read_text(encoding="utf-8"))
            old_count = len(old_data.get("products", []))
        except (OSError, ValueError, AttributeError):
            pass
    if old_count >= 5 and len(products) < max(2, int(old_count * 0.45)):
        raise RuntimeError(f"Unexpected catalogue drop ({old_count} -> {len(products)}); old catalogue preserved.")

    payload = {
        "store_url": STORE_URL,
        "vendor_id": vendor_id,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "basalam-public-storefront-scrape",
        "products": products,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Successfully scraped {len(products)} products from Nana Accessory (vendor {vendor_id}).")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
