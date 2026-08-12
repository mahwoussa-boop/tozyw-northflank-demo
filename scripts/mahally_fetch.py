#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mahally_fetch.py — جالب منتجات منصة محلي (mahally.com) عبر Algolia
====================================================================
يطبّق نفس طريقة محرّك المشروع (engines/mahally_scraper.py):
  • محلي مبنية على Salla ويبحث عبر Algolia (فهرس products_v2).
  • نطلب Algolia API مباشرة (أسرع 100x وبلا حظر HTML).
  • نتجاوز سقف Algolia (1000 نتيجة/استعلام) بتقسيم تلقائي:
        تصنيف فرعي (lvl2)  →  نطاقات سعرية  →  تقسيم نصفي متكرّر.
  • لكل منتج: كل الحقول + كل روابط الصور + اختيار «أفضل صورة» بدقّة عالية.

المخرجات (في مجلد --out):
  mahally_<تصنيف>_raw.json   — كل الحقول الخام (مرجع كامل).
  mahally_<تصنيف>.xlsx       — جدول نظيف جاهز للمراجعة/المطابقة/الرفع.
  (اختياري --download-images) images/<sku>.jpg — تنزيل أفضل صورة لكل منتج.

الاستخدام (من جذر المشروع، داخل .venv):
  python mahally_fetch.py                      # كل تصنيف «العطور والعود»
  python mahally_fetch.py --subcategory "عطور رجالية"
  python mahally_fetch.py --min-rating 4 --max 5000
  python mahally_fetch.py --category "جمال وعناية > مستحضرات التجميل"
  python mahally_fetch.py --list-subcategories # اعرض التصنيفات الفرعية وعددها فقط
  python mahally_fetch.py --download-images     # نزّل أفضل صورة لكل منتج

ملاحظة مفاتيح Algolia: مفتاح البحث العام مضمَّن أصلاً في موقع محلي (ليس سرّاً).
يُقرأ من متغيّرات البيئة ALGOLIA_APP_ID / ALGOLIA_API_KEY إن وُجدت (كما في محرّك
المشروع)، وإلا يستعمل القيمة العامة المكتشفة من الموقع كاحتياطي.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from threading import Lock

import requests

# ── إعداد الترميز على ويندوز ────────────────────────────────────────────────
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# ── ثوابت Algolia (مفتاح بحث عام مضمّن بالموقع — احتياطي) ────────────────────
APP_ID = os.environ.get("ALGOLIA_APP_ID") or "L41Y35UONW"
API_KEY = os.environ.get("ALGOLIA_API_KEY") or "f60e98a284e4b402af626d0dd1fc6cbd"
INDEX = os.environ.get("MAHALLY_INDEX", "products_v2")

# بروكسي محلي أولاً ثم نقطة Algolia المباشرة كاحتياطي
ENDPOINTS = [
    f"https://algolia.mahally.com/1/indexes/{INDEX}/query",
    f"https://{APP_ID}-dsn.algolia.net/1/indexes/{INDEX}/query",
]
HEADERS = {
    "X-Algolia-Application-Id": APP_ID,
    "X-Algolia-API-Key": API_KEY,
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
}

DEFAULT_CATEGORY = "جمال وعناية > العطور والعود"   # قيمة categories.lvl1
ALGOLIA_PAGE_CAP = 1000            # سقف Algolia لكل استعلام
HITS_PER_PAGE = 1000
MAX_WORKERS = 8
RETRIES = 3

# الحقول المطلوبة من كل منتج
ATTRS = [
    "name_ar", "name", "brand_name", "price", "regular_price", "sale_price",
    "discount_percentage", "image", "all_images", "images_count", "all_rating",
    "store_id", "store_name", "public_product_id", "categories", "status",
    "purchasable", "city",
]

_session = requests.Session()
_session.headers.update(HEADERS)
_seen_lock = Lock()
_print_lock = Lock()


# ═══════════════════════════════════════════════════════════════════════════
#  طبقة الشبكة
# ═══════════════════════════════════════════════════════════════════════════
def algolia_query(payload: dict) -> dict:
    """POST إلى Algolia مع تجربة النقاط الاحتياطية وإعادة المحاولة."""
    last_exc = None
    for endpoint in ENDPOINTS:
        for attempt in range(RETRIES):
            try:
                r = _session.post(endpoint, json=payload, timeout=25)
                if r.status_code == 200:
                    return r.json()
                if r.status_code in (429, 503):
                    time.sleep(1 + attempt)
                    continue
                last_exc = f"HTTP {r.status_code}"
                break
            except Exception as exc:  # noqa: BLE001
                last_exc = str(exc)
                time.sleep(1 + attempt)
    raise RuntimeError(f"Algolia query failed: {last_exc}")


def _base_filters(min_rating: int) -> str:
    """فلاتر تُطابق ما يعرضه الموقع (قابلة للتخفيف عبر --all)."""
    parts = ["purchasable:true", "visible_on:web"]
    if min_rating:
        parts.append(f"(all_rating.ranking>={min_rating} OR all_rating.ranking=0)")
    return " AND ".join(parts)


def count_hits(category: str, subcategory: str, min_rating: int,
               price_filter: str = "") -> int:
    facet = _facet_filters(category, subcategory)
    filt = _base_filters(min_rating)
    if price_filter:
        filt = f"{filt} AND {price_filter}" if filt else price_filter
    data = algolia_query({
        "query": "", "hitsPerPage": 0, "facetFilters": facet, "filters": filt,
    })
    return int(data.get("nbHits", 0))


def _facet_filters(category: str, subcategory: str):
    facet = [[f"categories.lvl1:{category}"]]
    if subcategory:
        # lvl2 يأتي بالشكل: "<lvl1> > <sub>"
        lvl2 = subcategory if " > " in subcategory else f"{category} > {subcategory}"
        facet.append([f"categories.lvl2:{lvl2}"])
    return facet


# ═══════════════════════════════════════════════════════════════════════════
#  استخراج الحقول
# ═══════════════════════════════════════════════════════════════════════════
def _price(hit: dict, field: str):
    """price.SA.SAR المتداخل → رقم (أو None)."""
    v = hit.get(field)
    if isinstance(v, dict):
        try:
            return float(v.get("SA", {}).get("SAR"))
        except (TypeError, ValueError):
            return None
    if isinstance(v, (int, float)):
        return float(v)
    return None


def _hd(url: str) -> str:
    """يرفع دقّة صورة cdn.salla.sa إلى 1000px إن كانت عبر cdn-cgi/image."""
    if not url:
        return url
    return re.sub(r"width=\d+,height=\d+", "width=1000,height=1000", url)


def _best_images(hit: dict) -> list:
    """قائمة صور فريدة بدقّة عالية، الرئيسية أولاً (سقف 10)."""
    out, seen = [], set()
    for u in [hit.get("image", ""), *(hit.get("all_images") or [])]:
        if not isinstance(u, str) or not u.strip():
            continue
        hd = _hd(u.strip())
        if hd not in seen:
            seen.add(hd)
            out.append(hd)
            if len(out) >= 10:
                break
    return out


def parse_hit(hit: dict) -> dict | None:
    name = hit.get("name_ar") or (
        hit.get("name", {}).get("ar") if isinstance(hit.get("name"), dict) else hit.get("name")
    ) or ""
    if not name or len(str(name)) < 2:
        return None
    price = _price(hit, "sale_price") or _price(hit, "price")
    if not price or price <= 0:
        return None
    regular = _price(hit, "regular_price") or price

    brand = hit.get("brand_name")
    if isinstance(brand, dict):
        brand = brand.get("ar") or brand.get("en") or ""

    cats = hit.get("categories") or {}
    lvl2 = ""
    if isinstance(cats, dict):
        v = cats.get("lvl2") or cats.get("lvl1") or cats.get("lvl0") or ""
        lvl2 = v[0] if isinstance(v, list) and v else (v if isinstance(v, str) else "")

    sku = str(hit.get("public_product_id") or hit.get("objectID") or "")
    store_id = hit.get("store_id") or ""
    url = f"https://mahally.com/products/{store_id}/{sku}" if store_id and sku else ""

    rating = hit.get("all_rating") or {}
    rc = int(rating.get("count", 0) or 0) if isinstance(rating, dict) else 0
    ra = float(rating.get("average", 0) or 0) if isinstance(rating, dict) else 0.0

    status = str(hit.get("status") or "").strip().lower()
    avail = False if status == "out" else bool(hit.get("purchasable", True))

    imgs = _best_images(hit)
    return {
        "sku": sku,
        "store": hit.get("store_name") or "",
        "store_id": store_id,
        "brand": (brand or "").strip(),
        "name": str(name).strip()[:300],
        "category": lvl2,
        "price": round(price, 2),
        "regular_price": round(regular, 2),
        "discount_pct": round(float(hit.get("discount_percentage") or 0), 2),
        "rating_avg": round(ra, 2) if rc else None,
        "rating_count": rc,
        "available": int(avail),
        "city": hit.get("city") or "",
        "product_url": url,
        "best_image": imgs[0] if imgs else "",
        "images_count": int(hit.get("images_count") or len(imgs)),
        "all_images": imgs,
    }


# ═══════════════════════════════════════════════════════════════════════════
#  الجلب مع التقسيم المتكرّر
# ═══════════════════════════════════════════════════════════════════════════
def _fetch_partition(category, subcategory, min_rating, price_filter,
                     seen: set, out: list):
    """يجلب منتجات فلتر سعري واحد؛ يقسّم نصفياً إذا > السقف."""
    facet = _facet_filters(category, subcategory)
    filt = _base_filters(min_rating)
    if price_filter:
        filt = f"{filt} AND {price_filter}" if filt else price_filter

    # كل الصفحات ضمن هذا الفلتر
    page, nb = 0, None
    while True:
        data = algolia_query({
            "query": "", "hitsPerPage": HITS_PER_PAGE, "page": page,
            "facetFilters": facet, "filters": filt,
            "attributesToRetrieve": ATTRS,
        })
        hits = data.get("hits", [])
        nb = data.get("nbHits", 0) if nb is None else nb
        for h in hits:
            pid = str(h.get("public_product_id") or h.get("objectID") or "")
            with _seen_lock:
                if not pid or pid in seen:
                    continue
                seen.add(pid)
            p = parse_hit(h)
            if p:
                out.append(p)
        page += 1
        if page >= data.get("nbPages", 1) or page * HITS_PER_PAGE >= ALGOLIA_PAGE_CAP:
            break
    return nb or 0


def _price_ranges():
    r = []
    for s in range(0, 500, 50):
        r.append((s, s + 50))
    for s in range(500, 2000, 200):
        r.append((s, s + 200))
    for s in range(2000, 10000, 1000):
        r.append((s, s + 1000))
    r.append((10000, 10_000_000))
    return r


def _split_range(category, subcategory, min_rating, lo, hi, seen, out, depth=0):
    pf = f"price.SA.SAR:{lo} TO {hi}"
    nb = count_hits(category, subcategory, min_rating, pf)
    if nb == 0:
        return
    if nb <= ALGOLIA_PAGE_CAP or depth > 25 or (hi - lo) <= 1:
        _fetch_partition(category, subcategory, min_rating, pf, seen, out)
        with _print_lock:
            print(f"    [{lo:>6}-{hi:<8}] {nb:>5} → {len(out):,} إجمالي", flush=True)
        return
    mid = (lo + hi) // 2 if (hi - lo) > 2 else lo + 1
    _split_range(category, subcategory, min_rating, lo, mid, seen, out, depth + 1)
    _split_range(category, subcategory, min_rating, mid, hi, seen, out, depth + 1)


def fetch_category(category, subcategory, min_rating, max_items=0):
    seen: set = set()
    out: list = []
    total = count_hits(category, subcategory, min_rating)
    label = subcategory or category
    print(f"▶ «{label}» — {total:,} منتج متوقع (min_rating={min_rating})", flush=True)

    ranges = _price_ranges()
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = [ex.submit(_split_range, category, subcategory, min_rating,
                          lo, hi, seen, out) for lo, hi in ranges]
        for _ in as_completed(futs):
            if max_items and len(out) >= max_items:
                break
    if max_items:
        out = out[:max_items]
    cov = (len(out) / total * 100) if total else 0
    print(f"✔ تم جلب {len(out):,}/{total:,} ({cov:.0f}%)", flush=True)
    return out


# ═══════════════════════════════════════════════════════════════════════════
#  المخرجات
# ═══════════════════════════════════════════════════════════════════════════
def _safe(name: str) -> str:
    return re.sub(r"[^\w؀-ۿ]+", "_", name).strip("_")[:60]


def write_outputs(products, out_dir, label, download_images=False):
    os.makedirs(out_dir, exist_ok=True)
    base = _safe(label)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")

    json_path = os.path.join(out_dir, f"mahally_{base}_raw_{stamp}.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(products, f, ensure_ascii=False, indent=1)
    print(f"  📄 JSON: {json_path}", flush=True)

    cols = ["sku", "store", "brand", "name", "category", "price",
            "regular_price", "discount_pct", "rating_avg", "rating_count",
            "available", "images_count", "best_image", "all_images",
            "product_url", "city", "store_id"]
    try:
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.title = "محلي"
        ws.append(cols)
        for p in products:
            row = dict(p)
            row["all_images"] = " , ".join(p.get("all_images") or [])
            ws.append([row.get(c, "") for c in cols])
        xlsx_path = os.path.join(out_dir, f"mahally_{base}_{stamp}.xlsx")
        wb.save(xlsx_path)
        print(f"  📊 XLSX: {xlsx_path}", flush=True)
    except Exception as exc:  # noqa: BLE001
        import csv
        csv_path = os.path.join(out_dir, f"mahally_{base}_{stamp}.csv")
        with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(cols)
            for p in products:
                row = dict(p)
                row["all_images"] = " , ".join(p.get("all_images") or [])
                w.writerow([row.get(c, "") for c in cols])
        print(f"  📊 CSV (openpyxl غير متاح: {exc}): {csv_path}", flush=True)

    if download_images:
        img_dir = os.path.join(out_dir, f"images_{base}")
        os.makedirs(img_dir, exist_ok=True)
        print(f"  ⬇ تنزيل أفضل صورة لكل منتج → {img_dir}", flush=True)
        _download_images(products, img_dir)


def _download_images(products, img_dir):
    def _one(p):
        url = p.get("best_image")
        if not url:
            return
        dest = os.path.join(img_dir, f"{p['sku']}.jpg")
        if os.path.exists(dest):
            return
        try:
            r = _session.get(url, timeout=25)
            if r.status_code == 200:
                with open(dest, "wb") as f:
                    f.write(r.content)
        except Exception:
            pass
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        list(ex.map(_one, products))


# ═══════════════════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════════════════
def list_subcategories(category, min_rating):
    data = algolia_query({
        "query": "", "hitsPerPage": 0, "facets": ["categories.lvl2"],
        "maxValuesPerFacet": 50,
        "facetFilters": [[f"categories.lvl1:{category}"]],
        "filters": _base_filters(min_rating),
    })
    facets = (data.get("facets") or {}).get("categories.lvl2", {})
    print(f"التصنيفات الفرعية لـ «{category}»:")
    for k, v in sorted(facets.items(), key=lambda x: -x[1]):
        print(f"  {v:>9,}  {k}")


def main():
    ap = argparse.ArgumentParser(description="جالب منتجات محلي عبر Algolia")
    ap.add_argument("--category", default=DEFAULT_CATEGORY, help="قيمة categories.lvl1")
    ap.add_argument("--subcategory", default="", help="تصنيف فرعي lvl2 (مثل: عطور رجالية)")
    ap.add_argument("--min-rating", type=int, default=4,
                    help="أدنى تصنيف ثقة (0=بلا فلتر، الافتراضي 4 كما بالموقع)")
    ap.add_argument("--max", type=int, default=0, help="حدّ أقصى للمنتجات (0=الكل)")
    ap.add_argument("--out", default="exports/mahally", help="مجلد المخرجات")
    ap.add_argument("--download-images", action="store_true", help="نزّل أفضل صورة لكل منتج")
    ap.add_argument("--list-subcategories", action="store_true",
                    help="اعرض التصنيفات الفرعية وعددها فقط")
    args = ap.parse_args()

    if args.list_subcategories:
        list_subcategories(args.category, args.min_rating)
        return

    t0 = time.time()
    products = fetch_category(args.category, args.subcategory, args.min_rating, args.max)
    products.sort(key=lambda p: (p.get("rating_count") or 0), reverse=True)
    label = args.subcategory or args.category
    write_outputs(products, args.out, label, args.download_images)
    print(f"🏁 انتهى في {time.time() - t0:.0f}s — {len(products):,} منتج", flush=True)


if __name__ == "__main__":
    main()
