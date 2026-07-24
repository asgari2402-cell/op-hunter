#!/usr/bin/env python3
from __future__ import annotations
import html
import json
import re
import urllib.parse
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
PRODUCTS_FILE = ROOT / "data" / "products.json"
BASE = "https://en.onepiece-cardgame.com"
INDEXES = [
    BASE + "/products/",
    BASE + "/topics/?tags=products",
]
UA = "Mozilla/5.0 AppleWebKit/537.36 Chrome/124 Safari/537.36 OP-Hunter-Catalog/1.0"

def fetch(url):
    req = Request(url, headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
    with urlopen(req, timeout=25) as r:
        raw = r.read(3_000_000)
        enc = r.headers.get_content_charset() or "utf-8"
        return raw.decode(enc, "ignore")

def clean(value):
    value = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", html.unescape(value)).strip()

def code_from(text, url):
    candidates = " ".join([text, urllib.parse.unquote(url)])
    patterns = [
        r"\b(OP[- ]?\d{1,2}(?:[-/ ]EB[- ]?\d{1,2})?)\b",
        r"\b(EB[- ]?\d{1,2})\b", r"\b(ST[- ]?\d{1,2})\b",
        r"\b(PRB[- ]?\d{1,2})\b", r"\b(DP[- ]?\d{1,2})\b",
        r"\b(TS[- ]?\d{1,2})\b", r"\b(LD[- ]?\d{1,2})\b",
    ]
    for p in patterns:
        m = re.search(p, candidates, re.I)
        if m:
            code = re.sub(r"\s+", "", m.group(1).upper()).replace("/", "-")
            code = re.sub(r"^(OP|EB|ST|PRB|DP|TS|LD)(\d)", r"\1-\2", code)
            return code
    return ""

def category(name, code):
    upper = (name + " " + code).upper()
    if "STARTER" in upper or code.startswith("ST-"): return "Starter Deck"
    if "EXTRA BOOSTER" in upper or code.startswith("EB-"): return "Extra Booster"
    if "PREMIUM BOOSTER" in upper or code.startswith("PRB-"): return "Premium Booster"
    if "BOOSTER" in upper or code.startswith("OP-"): return "Booster"
    if "DOUBLE PACK" in upper or code.startswith("DP-"): return "Double Pack"
    if "TIN" in upper or code.startswith("TS-"): return "Tin"
    if "ANNIVERSARY" in upper: return "Anniversary"
    if "PLAYMAT" in upper or "SLEEVE" in upper: return "Zubehör"
    if "PREMIUM CARD COLLECTION" in upper: return "Premium Bandai"
    return "Special Product"

def release_from(raw):
    # Prefer labels near Release Date / Available.
    patterns = [
        r"(?:Release Date|Available|On Sale)[^<:\n]{0,40}[:\s]*</?[^>]*>\s*([^<]{3,60})",
        r"(?:Release Date|Available|On Sale)\s*[:\-]\s*([^<\n]{3,60})",
        r"\b((?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+20\d{2})\b",
    ]
    for p in patterns:
        m = re.search(p, raw, re.I)
        if m:
            value = clean(m.group(1))
            if len(value) <= 60:
                return value
    return "Termin offen"

def discover_links(raw, source):
    links = []
    for href, label in re.findall(r'href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', raw, re.I | re.S):
        url = urllib.parse.urljoin(source, html.unescape(href))
        if not url.startswith(BASE + "/products/"):
            continue
        if url.rstrip("/") == (BASE + "/products").rstrip("/"):
            continue
        if not re.search(r"\.(?:html|php)(?:$|\?)", url, re.I):
            continue
        links.append((url.split("#")[0], clean(label)))
    return links

def parse_product(url, hint):
    raw = fetch(url)
    title = ""
    for p in [
        r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)',
        r"<title[^>]*>(.*?)</title>",
        r"<h1[^>]*>(.*?)</h1>",
    ]:
        m = re.search(p, raw, re.I | re.S)
        if m:
            title = clean(m.group(1))
            break
    title = re.sub(r"\s*[|−-]\s*ONE PIECE CARD GAME.*$", "", title, flags=re.I).strip()
    if not title:
        title = hint
    code = code_from(title, url)
    if not code and not any(k in title.upper() for k in [
        "ONE PIECE", "BOOSTER", "STARTER", "CARD COLLECTION", "PLAYMAT",
        "SLEEVE", "ILLUSTRATION BOX", "ANNIVERSARY", "DOUBLE PACK", "TIN"
    ]):
        return None
    return {
        "code": code or re.sub(r"[^A-Z0-9]+", "-", title.upper()).strip("-")[:30],
        "name": re.sub(r"\s*\[[^\]]+\]\s*$", "", title).strip(),
        "category": category(title, code),
        "release": release_from(raw),
        "official": url,
        "source": "official-auto",
    }

def main():
    existing = json.loads(PRODUCTS_FILE.read_text(encoding="utf-8"))
    by_url = {p.get("official", "").rstrip("/"): p for p in existing if p.get("official")}
    by_code = {p.get("code", "").upper(): p for p in existing if p.get("code")}
    found = {}
    for index in INDEXES:
        try:
            raw = fetch(index)
        except Exception as exc:
            print("Index nicht erreichbar:", index, type(exc).__name__)
            continue
        for url, hint in discover_links(raw, index):
            found[url.rstrip("/")] = (url, hint)

    added = updated = 0
    for key, (url, hint) in found.items():
        try:
            product = parse_product(url, hint)
        except Exception as exc:
            print("Produktseite übersprungen:", url, type(exc).__name__)
            continue
        if not product:
            continue
        current = by_url.get(key) or by_code.get(product["code"].upper())
        if current:
            changed = False
            for field in ("name", "category", "release", "official"):
                if product.get(field) and product[field] != "Termin offen" and current.get(field) != product[field]:
                    current[field] = product[field]
                    changed = True
            if changed:
                updated += 1
        else:
            existing.append(product)
            by_url[key] = product
            by_code[product["code"].upper()] = product
            added += 1

    existing.sort(key=lambda p: (p.get("category", ""), p.get("code", ""), p.get("name", "")))
    PRODUCTS_FILE.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Offizieller Katalog aktualisiert: {added} neu, {updated} geändert, {len(existing)} insgesamt.")

if __name__ == "__main__":
    main()
