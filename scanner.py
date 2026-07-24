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

def status(raw,query):
    visible=text(raw)
    low=visible.lower()
    if not relevant(visible[:160000],query):return None
    neg=any(re.search(p,low,re.I) for p in NEGATIVE)
    strong=bool(re.search(r"schema\.org/(?:InStock|PreOrder|LimitedAvailability)|add to cart|in den warenkorb|jetzt kaufen|buy now",raw,re.I))
    pos=strong or any(re.search(p,low,re.I) for p in POSITIVE)
    if not pos or (neg and not strong):return None
    typ="Vorbestellung" if re.search(r"\bvorbestell|\bpre[- ]?order|schema\.org/PreOrder",low,re.I) else "Bestellung"
    pm=PRICE.search(visible)
    return typ,pm.group(0) if pm else ""

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
    return out[:3]

def inspect(shop,product):
    query=f'{product.get("code","")} {product.get("name","")}'.strip()
    for su in search_urls(shop,query):
        try: raw=fetch(su)
        except Exception: continue
        hit=status(raw,query)
        if hit:
            return offer(shop,product,su,hit)
        for u in product_links(raw,su,shop,query):
            try: page=fetch(u)
            except Exception: continue
            hit=status(page,query)
            if hit:return offer(shop,product,u,hit)
        # Stop after the first working shop search endpoint to reduce traffic.
        if len(raw)>5000:break
    return None

def offer(shop,product,url,hit):
    return {
      "product_code":product.get("code",""),"product_name":product.get("name",""),
      "query":f'{product.get("code","")} {product.get("name","")}'.strip(),
      "shop":shop["name"],"country":shop.get("country",""),"url":url,
      "available":True,"order_type":hit[0],"price":hit[1]
    }

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
    result={
      "generated_at":dt.datetime.now(dt.timezone.utc).isoformat(),
      "checked_shops":len(SHOPS),"products_checked":len(PRODUCTS),
      "checks_attempted":len(tasks),"duration_seconds":round(time.time()-started,1),
      "offers":offers
    }
    OUTPUT.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print(f"Fertig: {len(offers)} verfügbare Angebote gespeichert.")

if __name__=="__main__":
    main()
