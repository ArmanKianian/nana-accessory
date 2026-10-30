#!/usr/bin/env python3
"""Sync ONLY Nana Accessory's catalogue from Basalam's vendor-products API.

The storefront HTML is used solely to verify the canonical vendor identity.
Product discovery never scans arbitrary links or unrelated product pages.
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "src" / "data" / "products.json"
STORE_URL = "https://basalam.com/nana-accessory"
EXPECTED_IDENTIFIER = "nana-accessory"
EXPECTED_VENDOR_ID = 1_161_426
# Public vendor catalogue endpoint used by Basalam's vendor-products service.
API_URL = "https://services.basalam.com/v1/vendors/{vendor_id}/products"
PER_PAGE = 100
MAX_PAGES = 20
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/128.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "fa-IR,fa;q=0.9,en;q=0.7",
    "Origin": "https://basalam.com",
    "Referer": STORE_URL,
}


def read_store_identity(session: requests.Session) -> tuple[int, int | None]:
    response = session.get(STORE_URL, timeout=35)
    response.raise_for_status()
    html = response.text
    match = re.search(
        r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
        html,
        re.I | re.S,
    )
    if not match:
        raise RuntimeError("Basalam storefront did not include its Next.js store data.")
    try:
        next_data = json.loads(match.group(1))
        queries = next_data["props"]["pageProps"]["dehydratedState"]["queries"]
    except (ValueError, KeyError, TypeError) as exc:
        raise RuntimeError("Could not read the storefront's vendor identity.") from exc

    def walk(value: Any):
        if isinstance(value, dict):
            yield value
            for child in value.values():
                yield from walk(child)
        elif isinstance(value, list):
            for child in value:
                yield from walk(child)

    vendor = None
    for query in queries:
        state_data = query.get("state", {}).get("data")
        vendor = next(
            (candidate for candidate in walk(state_data)
             if candidate.get("identifier") == EXPECTED_IDENTIFIER),
            None,
        )
        if vendor:
            break

    if not vendor:
        raise RuntimeError(f"Store page did not identify vendor '{EXPECTED_IDENTIFIER}'.")
    vendor_id = int(vendor.get("id", 0))
    if vendor_id != EXPECTED_VENDOR_ID:
        raise RuntimeError(
            f"Vendor identity mismatch: expected {EXPECTED_VENDOR_ID}, got {vendor_id}."
        )
    advertised_count = vendor.get("productCount")
    return vendor_id, int(advertised_count) if advertised_count is not None else None


def unwrap_products(payload: Any) -> tuple[list[dict], dict]:
    """Accept the API's common envelope shapes, but never guess from HTML links."""
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)], {}
    if not isinstance(payload, dict):
        raise RuntimeError("Vendor products API returned an unexpected response type.")
    for key in ("data", "products", "items", "result"):
        value = payload.get(key)
        if isinstance(value, list):
            return [x for x in value if isinstance(x, dict)], payload
        if isinstance(value, dict):
            for nested_key in ("data", "products", "items"):
                nested = value.get(nested_key)
                if isinstance(nested, list):
                    return [x for x in nested if isinstance(x, dict)], value
    raise RuntimeError("Could not find the product list in the vendor API response.")


def first_image(product: dict) -> str:
    for key in ("photo", "photos", "images", "image", "gallery", "cover"):
        value = product.get(key)
        values = value if isinstance(value, list) else [value]
        for entry in values:
            if isinstance(entry, str) and entry.startswith("https://"):
                return entry
            if isinstance(entry, dict):
                for image_key in ("original", "lg", "md", "url", "src"):
                    candidate = entry.get(image_key)
                    if isinstance(candidate, str) and candidate.startswith("https://"):
                        return candidate
    return ""


def to_int(value: Any) -> int | None:
    if isinstance(value, dict):
        for key in ("amount", "value", "price"):
            parsed = to_int(value.get(key))
            if parsed is not None:
                return parsed
        return None
    if isinstance(value, (int, float)) and value > 0:
        return int(value)
    if isinstance(value, str):
        digits = re.sub(r"[^0-9]", "", value)
        return int(digits) if digits else None
    return None


def normalize_product(raw: dict) -> dict | None:
    # Vendor API product records should carry vendor_id. If present, enforce it.
    owner = raw.get("vendor_id")
    if owner is None and isinstance(raw.get("vendor"), dict):
        owner = raw["vendor"].get("id")
    if owner is not None and int(owner) != EXPECTED_VENDOR_ID:
        return None

    product_id = raw.get("id") or raw.get("product_id")
    title = raw.get("name") or raw.get("title")
    if not product_id or not isinstance(title, str) or len(title.strip()) < 2:
        return None

    price_raw = None
    for key in ("primary_price", "price", "final_price", "selling_price"):
        if raw.get(key) is not None:
            price_raw = to_int(raw[key])
            if price_raw:
                break
    # Basalam's API price values are in rials; the storefront displays tomans.
    price_toman = round(price_raw / 10) if price_raw else None

    slug = raw.get("slug") or raw.get("identifier")
    product_url = f"https://basalam.com/p/{quote(str(product_id))}"
    if slug:
        product_url += f"/{quote(str(slug))}"

    return {
        "id": str(product_id),
        "name": title.strip(),
        "url": product_url,
        "image": first_image(raw),
        "price": price_toman,
    }


def fetch_all_products(session: requests.Session, vendor_id: int) -> list[dict]:
    all_products: dict[str, dict] = {}
    for page in range(1, MAX_PAGES + 1):
        response = session.get(
            API_URL.format(vendor_id=vendor_id),
            params={"page": page, "per_page": PER_PAGE, "sort": "id:asc", "variants_flatting": "false"},
            timeout=35,
        )
        response.raise_for_status()
        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError("Vendor products endpoint did not return JSON.") from exc
        records, metadata = unwrap_products(payload)
        for raw in records:
            product = normalize_product(raw)
            if product:
                all_products[product["id"]] = product

        total_pages = metadata.get("total_page") or metadata.get("total_pages")
        if total_pages is not None and page >= int(total_pages):
            break
        if len(records) < PER_PAGE:
            break
    else:
        raise RuntimeError(f"Reached the {MAX_PAGES}-page safety limit; refusing partial sync.")

    return list(all_products.values())


def main() -> None:
    session = requests.Session()
    session.headers.update(HEADERS)
    vendor_id, advertised_count = read_store_identity(session)
    products = fetch_all_products(session, vendor_id)

    if not products:
        raise RuntimeError("Nana's vendor API returned zero valid products; old catalogue preserved.")
    if advertised_count is not None and len(products) != advertised_count:
        raise RuntimeError(
            f"Catalogue count mismatch: storefront says {advertised_count}, API returned {len(products)}. "
            "Old catalogue preserved to avoid publishing an incomplete list."
        )

    old = {}
    if OUTPUT.exists():
        try:
            old = json.loads(OUTPUT.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            old = {}
    old_count = len(old.get("products", [])) if isinstance(old, dict) else 0
    if old_count >= 5 and len(products) < max(2, int(old_count * 0.45)):
        raise RuntimeError(f"Unexpected catalogue drop ({old_count} -> {len(products)}); old catalogue preserved.")

    products.sort(key=lambda item: item["id"])
    payload = {
        "store_url": STORE_URL,
        "vendor_id": vendor_id,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "basalam-vendor-products-api",
        "products": products,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Synced {len(products)} Nana Accessory products (vendor {vendor_id}).")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
