#!/usr/bin/env python3
"""Refresh the static product catalogue from Nana's public Basalam storefront.

This deliberately fails closed: if Basalam changes its markup or returns an
incomplete/blocked response, the previous catalogue is kept rather than
publishing an empty shop. For long-term guaranteed access, use Basalam's
official API credentials (see README) once the vendor API endpoints are enabled.
"""
from __future__ import annotations
import json, os, re, sys, time
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'src' / 'data' / 'products.json'
STORE_URL = 'https://basalam.com/nana-accessory'
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8',
    'Accept-Language': 'fa-IR,fa;q=0.9,en;q=0.7',
}

class Links(HTMLParser):
    def __init__(self):
        super().__init__(); self.hrefs=[]
    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            href = dict(attrs).get('href')
            if href: self.hrefs.append(href)

def walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values(): yield from walk(child)
    elif isinstance(value, list):
        for child in value: yield from walk(child)

def image_url(value):
    if isinstance(value, str) and value.startswith('http'):
        return value
    if isinstance(value, dict):
        for key in ('original', 'lg', 'large', 'url', 'md', 'src'):
            found = value.get(key)
            if isinstance(found, str) and found.startswith('http'): return found
        for child in value.values():
            found = image_url(child)
            if found: return found
    if isinstance(value, list):
        for child in value:
            found = image_url(child)
            if found: return found
    return ''

def product_from_dict(obj):
    title = next((obj.get(k) for k in ('name','title','product_name','fa_name') if isinstance(obj.get(k),str) and len(obj.get(k).strip()) > 2), None)
    if not title: return None
    url = ''
    for key in ('url','web_url','link','canonical_url'):
        candidate = obj.get(key)
        if isinstance(candidate,str) and 'basalam.com' in candidate: url=candidate; break
    slug = obj.get('slug') or obj.get('identifier')
    pid = obj.get('id') or obj.get('product_id')
    if not url:
        candidate = obj.get('path')
        if isinstance(candidate,str) and candidate.startswith('/'): url=urljoin('https://basalam.com',candidate)
        elif isinstance(slug,str) and slug: url=f'https://basalam.com/nana-accessory/product/{slug}'
    if not url: return None
    pic = ''
    for key in ('image','images','photo','photos','cover','thumbnail','main_image'):
        if key in obj:
            pic = image_url(obj[key])
            if pic: break
    price = None
    for key in ('price','final_price','price_amount','amount','selling_price'):
        value = obj.get(key)
        if isinstance(value,(int,float)) and value > 0:
            price = int(value); break
        if isinstance(value,dict):
            for subkey in ('amount','value','price'):
                if isinstance(value.get(subkey),(int,float)) and value[subkey] > 0:
                    price=int(value[subkey]); break
        if price: break
    # Basalam's public web page may expose amounts in rials or tomans depending
    # on its data layer. Preserve the numeric source value; verify the unit on
    # the linked product page before relying on displayed prices.
    return {'id': str(pid or slug or url), 'name': title.strip(), 'url': url, 'image': pic, 'price': price}

def json_blobs(html):
    # JSON-LD and Next/Nuxt application state are the most stable public sources.
    for match in re.finditer(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',html,re.I|re.S):
        try: yield json.loads(match.group(1))
        except Exception: pass
    for match in re.finditer(r'<script[^>]+(?:id=["\']__NEXT_DATA__["\']|type=["\']application/json["\'])[^>]*>(.*?)</script>',html,re.I|re.S):
        try: yield json.loads(match.group(1))
        except Exception: pass
    # Framework hydration payloads sometimes escape JSON inside script text.
    for match in re.finditer(r'\{[^\n]{80,}\}',html):
        raw=match.group(0)
        if any(k in raw for k in ('product_name','"product"','"price"','"images"')):
            try: yield json.loads(raw)
            except Exception: pass

def main():
    session=requests.Session(); session.headers.update(HEADERS)
    response=session.get(STORE_URL,timeout=35); response.raise_for_status()
    if len(response.text) < 3000: raise RuntimeError('Basalam returned an unexpectedly small page.')
    products={}
    for blob in json_blobs(response.text):
        for obj in walk(blob):
            item=product_from_dict(obj)
            if item: products[item['url']]=item
    # The public HTML sometimes includes product-card links even when product
    # data is hydrated separately. Fetch those pages to parse their metadata.
    parser=Links(); parser.feed(response.text)
    product_urls=[]
    for href in parser.hrefs:
        full=urljoin(STORE_URL+'/',href)
        path=urlparse(full).path
        if urlparse(full).netloc.endswith('basalam.com') and re.search(r'/(product|p)/',path) and full not in product_urls:
            product_urls.append(full)
    for url in product_urls[:100]:
        if url in products: continue
        try:
            page=session.get(url,timeout=25); page.raise_for_status()
            for blob in json_blobs(page.text):
                for obj in walk(blob):
                    item=product_from_dict(obj)
                    if item and item['url']==url: products[url]=item
            time.sleep(.25)
        except requests.RequestException as exc:
            print(f'WARN: could not read {url}: {exc}',file=sys.stderr)
    # Deduplicate repeated nested representations and reject unrelated objects.
    result=sorted(products.values(),key=lambda x:x['name'])
    old={}
    if OUTPUT.exists():
        try: old=json.loads(OUTPUT.read_text(encoding='utf-8'))
        except Exception: old={}
    if not result:
        raise RuntimeError('No products could be extracted. Keeping the existing products.json unchanged.')
    # A storefront currently showing 18 products should not silently be
    # replaced by a tiny partial scrape. Permit a smaller count only if the
    # page itself clearly signals a smaller total or the old catalogue is empty.
    old_count=len(old.get('products',[])) if isinstance(old,dict) else 0
    count_match=re.search(r'(\d+)\s*محصول',response.text)
    advertised=int(count_match.group(1)) if count_match else None
    if advertised and len(result) < min(advertised, max(3, int(advertised*.65))):
        raise RuntimeError(f'Incomplete scrape: found {len(result)} of about {advertised} products. Previous catalogue preserved.')
    if old_count >= 5 and len(result) < max(2, int(old_count*.45)):
        raise RuntimeError(f'Unexpected catalogue drop ({old_count} -> {len(result)}). Previous catalogue preserved.')
    payload={'store_url':STORE_URL,'updated_at':datetime.now(timezone.utc).isoformat(timespec='seconds'),'source':'basalam-public-storefront','products':result}
    OUTPUT.parent.mkdir(parents=True,exist_ok=True)
    OUTPUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(f'Synced {len(result)} products to {OUTPUT}')

if __name__=='__main__':
    try: main()
    except Exception as exc:
        print(f'ERROR: {exc}',file=sys.stderr); sys.exit(1)
