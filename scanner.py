#!/usr/bin/env python3
from __future__ import annotations
import concurrent.futures
import datetime as dt
import html
import json
import os
import re
import time
import urllib.parse
from pathlib import Path
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
SHOPS = json.loads((ROOT / "data" / "shops.json").read_text(encoding="utf-8"))
PRODUCTS = json.loads((ROOT / "data" / "products.json").read_text(encoding="utf-8"))
OUTPUT = ROOT / "data" / "results.json"
EVENTS_FILE = ROOT / "data" / "events.json"
PRICE_HISTORY_FILE = ROOT / "data" / "price_history.json"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36 OP-Hunter-Cloud/1.0"
MAX_WORKERS = 18
TIMEOUT = 10

POSITIVE = [
 r"\bauf lager\b", r"\bsofort lieferbar\b", r"\bsofort verfügbar\b", r"\blieferbar\b",
 r"\bin stock\b", r"\bavailable\b", r"\badd to cart\b", r"\bin den warenkorb\b",
 r"\bjetzt kaufen\b", r"\bbuy now\b", r"\bvorbestell(?:ung|en|bar)?\b",
 r"\bpre[- ]?order(?: now)?\b", r"schema\.org/(?:InStock|PreOrder|LimitedAvailability)"
]
NEGATIVE = [
 r"\bnicht auf lager\b", r"\bnicht verfügbar\b", r"\bausverkauft\b", r"\bsold out\b",
 r"\bout of stock\b", r"\bderzeit nicht lieferbar\b", r"\bnicht lieferbar\b",
 r"schema\.org/OutOfStock"
]
PRICE = re.compile(r"(?:€\s?\d{1,4}(?:[.,]\d{2})?|\d{1,4}(?:[.,]\d{2})?\s?€|£\s?\d{1,4}(?:[.,]\d{2})?)", re.I)
HREF = re.compile(r'href\s*=\s*["\']([^"\']+)["\']', re.I)

def fetch(url):
    req=Request(url,headers={"User-Agent":UA,"Accept-Language":"de-DE,de;q=0.9,en;q=0.8"})
    with urlopen(req,timeout=TIMEOUT) as r:
        raw=r.read(1_800_000)
        charset=r.headers.get_content_charset() or "utf-8"
        return raw.decode(charset,"ignore")

def text(raw):
    raw=re.sub(r"<script\b[^>]*>.*?</script>"," ",raw,flags=re.I|re.S)
    raw=re.sub(r"<style\b[^>]*>.*?</style>"," ",raw,flags=re.I|re.S)
    return re.sub(r"\s+"," ",re.sub(r"<[^>]+>"," ",html.unescape(raw))).strip()

def domain(url):
    return urllib.parse.urlparse(url).netloc.lower().removeprefix("www.")

def tokens(query):
    stop={"one","piece","card","game","tcg","english","englisch","booster","display","box","deck","set"}
    return [x.lower() for x in re.findall(r"[A-Za-z0-9]+",query) if len(x)>1 and x.lower() not in stop]

def relevant(page_text,query):
    ts=tokens(query)
    if not ts:return False
    low=page_text.lower()
    hits=sum(t in low for t in ts)
    codes=re.findall(r"\b(?:OP|EB|ST|PRB|DP)[- ]?\d{1,2}\b",query,re.I)
    if codes and any(re.sub(r"[- ]","",c.lower()) in re.sub(r"[- ]","",low) for c in codes):
        hits+=2
    return hits/max(1,len(ts))>=0.60


def canonical_url(raw, fallback):
    patterns = [
        r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\']([^"\']+)',
        r'<meta[^>]+property=["\']og:url["\'][^>]+content=["\']([^"\']+)',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:url["\']',
    ]
    for pattern in patterns:
        m = re.search(pattern, raw, re.I)
        if m:
            return urllib.parse.urljoin(fallback, html.unescape(m.group(1))).split("#")[0]
    return fallback.split("#")[0]

def page_title(raw):
    patterns = [
        r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:title["\']',
        r'<h1[^>]*>(.*?)</h1>',
        r'<title[^>]*>(.*?)</title>',
    ]
    for pattern in patterns:
        m = re.search(pattern, raw, re.I | re.S)
        if m:
            return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(m.group(1)))).strip()
    return ""

def normalized_code(value):
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())

def exact_product_identity(raw, url, query):
    visible_title = page_title(raw)
    decoded_url = urllib.parse.unquote(url)
    codes = re.findall(r"\b(?:OP|EB|ST|PRB|DP|TS|LD)[- ]?\d{1,2}\b", query, re.I)
    if codes:
        wanted = {normalized_code(c) for c in codes}
        title_code = normalized_code(visible_title)
        url_code = normalized_code(decoded_url)
        raw_head = normalized_code(raw[:250000])
        if not any(c in title_code or c in url_code or c in raw_head for c in wanted):
            return False

    q_tokens = [t for t in tokens(query) if not re.fullmatch(r"(?:op|eb|st|prb|dp|ts|ld)\d+", normalized_code(t))]
    title_low = visible_title.lower()
    if q_tokens:
        meaningful = [t for t in q_tokens if len(t) >= 4]
        if meaningful:
            hits = sum(t in title_low for t in meaningful)
            # Require at least one meaningful name token in the main title.
            if hits == 0 and not codes:
                return False

    return True

def iter_jsonld(raw):
    for block in re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        raw, re.I | re.S
    ):
        try:
            data = json.loads(html.unescape(block).strip())
        except Exception:
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            item = stack.pop()
            if isinstance(item, dict):
                yield item
                graph = item.get("@graph")
                if isinstance(graph, list):
                    stack.extend(graph)
            elif isinstance(item, list):
                stack.extend(item)

def schema_product_offer(raw, query, page_url):
    wanted_codes = {
        normalized_code(c)
        for c in re.findall(r"\b(?:OP|EB|ST|PRB|DP|TS|LD)[- ]?\d{1,2}\b", query, re.I)
    }

    for item in iter_jsonld(raw):
        item_type = item.get("@type", "")
        types = item_type if isinstance(item_type, list) else [item_type]
        if not any(str(t).lower() == "product" for t in types):
            continue

        identity = " ".join(str(item.get(k, "")) for k in ("name", "sku", "mpn", "productID"))
        identity_compact = normalized_code(identity)
        if wanted_codes and not any(c in identity_compact or c in normalized_code(page_url) for c in wanted_codes):
            continue

        offers = item.get("offers")
        if isinstance(offers, dict):
            offers = [offers]
        if not isinstance(offers, list):
            continue

        for offer in offers:
            if not isinstance(offer, dict):
                continue
            availability = str(offer.get("availability", ""))
            if availability.endswith("/OutOfStock") or availability.endswith("/Discontinued"):
                continue
            if not any(availability.endswith("/" + x) for x in ("InStock", "PreOrder", "LimitedAvailability")):
                continue

            direct = offer.get("url") or item.get("url") or page_url
            direct = canonical_url(raw, urllib.parse.urljoin(page_url, str(direct)))

            price = offer.get("price")
            currency = offer.get("priceCurrency")
            if price not in (None, ""):
                price_text = f"{price} {currency}".strip()
            else:
                price_text = ""

            order_type = "Vorbestellung" if availability.endswith("/PreOrder") else "Bestellung"
            return {
                "order_type": order_type,
                "price": price_text,
                "url": direct,
                "evidence": "schema.org Product/Offer",
            }
    return None

def is_direct_product_page(raw, url, query):
    decoded_url = urllib.parse.unquote(url).lower()

    product_markup = bool(re.search(
        r'property=["\']og:type["\'][^>]+content=["\']product|'
        r'"@type"\s*:\s*"Product"|'
        r'itemtype=["\'][^"\']*schema\.org/Product|'
        r'class=["\'][^"\']*(?:product-detail|product-page|product-info)',
        raw, re.I
    ))
    product_url = any(x in decoded_url for x in ["/product/", "/products/", "/produkt/", "/p/"])

    if not (product_markup or product_url):
        return False
    return exact_product_identity(raw, url, query)

def status(raw, query, url):
    if not is_direct_product_page(raw, url, query):
        return None

    # Highest-confidence path: exact schema.org Product + Offer.
    schema = schema_product_offer(raw, query, url)
    if schema:
        return schema

    visible = text(raw)
    low = visible.lower()

    out_schema = bool(re.search(r'schema\.org/(?:OutOfStock|Discontinued)', raw, re.I))
    negative = any(re.search(p, low, re.I) for p in NEGATIVE)
    if out_schema or negative:
        return None

    # Fallback only when a clearly active purchase control exists on the exact product page.
    active_cart = bool(re.search(
        r'<(?:button|a)[^>]*(?:add.to.cart|in.den.warenkorb|jetzt.kaufen|buy.now)[^>]*>',
        raw, re.I
    )) and not bool(re.search(
        r'<(?:button|a)[^>]*(?:disabled|aria-disabled=["\']true["\'])[^>]*'
        r'(?:add.to.cart|in.den.warenkorb|jetzt.kaufen|buy.now)',
        raw, re.I
    ))

    preorder_text = bool(re.search(
        r'\bvorbestell(?:ung|en|bar)?\b|\bpre[- ]?order(?: now)?\b',
        low, re.I
    ))

    if not active_cart:
        return None

    return {
        "order_type": "Vorbestellung" if preorder_text else "Bestellung",
        "price": "",
        "url": canonical_url(raw, url),
        "evidence": "aktiver Kaufbutton",
    }

def search_urls(shop,query):
    q=urllib.parse.quote_plus(query)
    b=shop["url"].rstrip("/")
    return [
      f"{b}/search?sSearch={q}",f"{b}/search?q={q}",f"{b}/suche?sSearch={q}",
      f"{b}/suche?q={q}",f"{b}/catalogsearch/result/?q={q}",
      f"{b}/?s={q}&post_type=product",f"{b}/collections/all?q={q}"
    ]

def product_links(raw,base,shop,query):
    d=domain(shop["url"]); out=[]; ts=tokens(query)
    for href in HREF.findall(raw):
        u=urllib.parse.urljoin(base,html.unescape(href))
        if not u.startswith("http") or domain(u)!=d:continue
        low=urllib.parse.unquote(u).lower()
        if any(x in low for x in ["/cart","/warenkorb","/login","/account","/privacy","/impressum"]):continue
        score=sum(t in low for t in ts)+sum(x in low for x in ["/product","/products","/produkt","/p/"])
        if score and u not in out:out.append(u)
    codes = [
        re.sub(r"[- ]", "", c.lower())
        for c in re.findall(r"\b(?:OP|EB|ST|PRB|DP|TS|LD)[- ]?\d{1,2}\b", query, re.I)
    ]
    out.sort(key=lambda u: (
        not any(c in re.sub(r"[- ]", "", urllib.parse.unquote(u).lower()) for c in codes),
        not any(x in u.lower() for x in ["/product/", "/products/", "/produkt/", "/p/"]),
        len(u)
    ))
    return out[:8]

def inspect(shop,product):
    query=f'{product.get("code","")} {product.get("name","")}'.strip()
    for su in search_urls(shop,query):
        try: raw=fetch(su)
        except Exception: continue
        # Such- und Kategorieseiten dürfen niemals als kaufbares Angebot erscheinen.
        hit = None
        for u in product_links(raw,su,shop,query):
            try: page=fetch(u)
            except Exception: continue
            hit=status(page,query,u)
            if hit:return offer(shop,product,u,hit)
        # Stop after the first working shop search endpoint to reduce traffic.
        if len(raw)>5000:break
    return None

def offer(shop, product, url, hit):
    direct_url = hit.get("url") or url
    return {
      "product_code": product.get("code", ""),
      "product_name": product.get("name", ""),
      "query": f'{product.get("code","")} {product.get("name","")}'.strip(),
      "shop": shop["name"],
      "country": shop.get("country", ""),
      "url": direct_url,
      "available": True,
      "order_type": hit.get("order_type", "Bestellung"),
      "price": hit.get("price", ""),
      "price_verified": bool(hit.get("price")),
      "evidence": hit.get("evidence", ""),
    }


def load_json(path, fallback):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return fallback

def numeric_price(value):
    if not value:
        return None
    match = re.search(r"\d+(?:[.,]\d+)?", str(value))
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", "."))
    except ValueError:
        return None

def offer_key(offer):
    return f'{offer.get("product_code","")}::{offer.get("shop","")}'

def build_events(previous, current, timestamp):
    previous_map = {offer_key(o): o for o in previous.get("offers", [])}
    current_map = {offer_key(o): o for o in current.get("offers", [])}
    new_events = []

    for key, offer in current_map.items():
        old = previous_map.get(key)
        if old is None:
            new_events.append({
                "type": "new_offer",
                "title": "Neues verfügbares Angebot",
                "product_code": offer.get("product_code", ""),
                "product_name": offer.get("product_name", ""),
                "shop": offer.get("shop", ""),
                "order_type": offer.get("order_type", ""),
                "price": offer.get("price", ""),
                "url": offer.get("url", ""),
                "detected_at": timestamp,
            })
            continue

        old_price = numeric_price(old.get("price"))
        new_price = numeric_price(offer.get("price"))
        if old_price is not None and new_price is not None and abs(old_price - new_price) >= 0.01:
            new_events.append({
                "type": "price_drop" if new_price < old_price else "price_rise",
                "title": "Preis gesunken" if new_price < old_price else "Preis gestiegen",
                "product_code": offer.get("product_code", ""),
                "product_name": offer.get("product_name", ""),
                "shop": offer.get("shop", ""),
                "old_price": old.get("price", ""),
                "price": offer.get("price", ""),
                "url": offer.get("url", ""),
                "detected_at": timestamp,
            })

    for key, old in previous_map.items():
        if key not in current_map:
            new_events.append({
                "type": "removed_offer",
                "title": "Angebot nicht mehr bestätigt",
                "product_code": old.get("product_code", ""),
                "product_name": old.get("product_name", ""),
                "shop": old.get("shop", ""),
                "price": old.get("price", ""),
                "url": old.get("url", ""),
                "detected_at": timestamp,
            })

    stored = load_json(EVENTS_FILE, {"events": []}).get("events", [])
    merged = new_events + stored
    # Keep the latest 300 events.
    return {"generated_at": timestamp, "events": merged[:300]}

def update_price_history(current, timestamp):
    history = load_json(PRICE_HISTORY_FILE, {"products": {}})
    products = history.setdefault("products", {})
    date_key = timestamp[:10]

    grouped = {}
    for offer in current.get("offers", []):
        if not offer.get("price_verified"):
            continue
        value = numeric_price(offer.get("price"))
        if value is None:
            continue
        code = offer.get("product_code", "")
        if not code:
            continue
        grouped.setdefault(code, []).append({
            "value": value,
            "label": offer.get("price", ""),
            "shop": offer.get("shop", ""),
        })

    for code, entries in grouped.items():
        best = min(entries, key=lambda x: x["value"])
        series = products.setdefault(code, [])
        point = {
            "date": date_key,
            "value": best["value"],
            "label": best["label"],
            "shop": best["shop"],
        }
        if series and series[-1].get("date") == date_key:
            series[-1] = point
        else:
            series.append(point)
        products[code] = series[-120:]

    history["generated_at"] = timestamp
    return history

def main():
    # Two UTC cron entries handle German summer/winter time. Scheduled runs outside 06:xx Berlin exit.
    if os.getenv("GITHUB_EVENT_NAME")=="schedule":
        now=dt.datetime.now(ZoneInfo("Europe/Berlin"))
        if now.hour!=6:
            print("Nicht 06:00 Uhr Europe/Berlin – dieser DST-Ausgleichslauf wird übersprungen.")
            return

    tasks=[(s,p) for p in PRODUCTS for s in SHOPS]
    offers=[]; started=time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures=[pool.submit(inspect,s,p) for s,p in tasks]
        for i,f in enumerate(concurrent.futures.as_completed(futures),1):
            try:
                v=f.result()
                if v:offers.append(v)
            except Exception:
                pass
            if i%250==0:print(f"{i}/{len(tasks)} Prüfungen abgeschlossen")

    # De-duplicate exact shop/product hits.
    unique={}
    for o in offers:
        unique[(o["product_code"],o["shop"])]=o
    offers=list(unique.values())
    offers.sort(key=lambda x:(x["product_code"],x["order_type"]!="Bestellung",x["shop"].lower()))
    timestamp = dt.datetime.now(dt.timezone.utc).isoformat()
    previous = load_json(OUTPUT, {"offers": []})
    verified_prices = sum(1 for o in offers if o.get("price_verified"))
    preorders = sum(1 for o in offers if o.get("order_type") == "Vorbestellung")
    products_with_offers = len({o.get("product_code") for o in offers if o.get("product_code")})
    shops_with_offers = len({o.get("shop") for o in offers if o.get("shop")})

    result={
      "generated_at": timestamp,
      "checked_shops":len(SHOPS),
      "products_checked":len(PRODUCTS),
      "checks_attempted":len(tasks),
      "duration_seconds":round(time.time()-started,1),
      "offers_count":len(offers),
      "preorders_count":preorders,
      "verified_prices_count":verified_prices,
      "products_with_offers":products_with_offers,
      "shops_with_offers":shops_with_offers,
      "offers":offers
    }
    events = build_events(previous, result, timestamp)
    price_history = update_price_history(result, timestamp)

    OUTPUT.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    EVENTS_FILE.write_text(json.dumps(events,ensure_ascii=False,indent=2),encoding="utf-8")
    PRICE_HISTORY_FILE.write_text(json.dumps(price_history,ensure_ascii=False,indent=2),encoding="utf-8")
    print(f"Fertig: {len(offers)} verfügbare Angebote gespeichert.")

if __name__=="__main__":
    main()
