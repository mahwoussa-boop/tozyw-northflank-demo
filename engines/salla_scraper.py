"""
engines/salla_scraper.py — محرك كشط متاجر منصة سلة Salla السعودية
══════════════════════════════════════════════════════════════════════════════
منصة سلة هي منصة التجارة الإلكترونية الأكبر في السعودية. يستخدمها آلاف المتاجر
لبيع العطور، الملابس، الإلكترونيات، وغيرها.

يغطي هذا المحرك:
- كشط منتجات من أي متجر سلة
- استخراج بيانات المنتج (اسم، سعر، صورة، توفر)
- دعم API سلة المخفي
- كشط قائمة المتاجر من فئات مختلفة
"""
from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple

from engines.unified_competitor_scraper import (
    BaseCompetitorScraper, UnifiedProduct, SCRAPER_REGISTRY,
)

logger = logging.getLogger("SallaScraper")


class SallaStoreScraper(BaseCompetitorScraper):
    """محرك كشط متاجر منصة سلة السعودية."""
    
    SOURCE_NAME = "salla_store"
    
    def __init__(self, store_domain: str = "", delay: float = 1.5, proxy: str = "",
                 headers: Optional[Dict[str, str]] = None, timeout: int = 25,
                 max_retries: int = 3, verify_ssl: bool = True):
        super().__init__(delay=delay, proxy=proxy)
        self.store_domain = store_domain  # e.g. "https://store.salla.sa"
        self.store_name = store_domain.replace("https://", "").replace("http://", "").split(".")[0] if store_domain else ""
        self.headers = headers or {}
        self.timeout = timeout
        self.max_retries = max_retries
        self.verify_ssl = verify_ssl
    
    def scrape(self, category: str = "", pages: int = 1, search: str = "") -> List[UnifiedProduct]:
        """كشط متجر سلة."""
        if not self.store_domain:
            logger.error("No store domain provided")
            return []
        
        products = []
        
        for page in range(1, pages + 1):
            if search:
                url = f"{self.store_domain}/search?q={search}&page={page}"
            elif category:
                url = f"{self.store_domain}/category/{category}?page={page}"
            else:
                url = f"{self.store_domain}/products?page={page}"
            
            logger.info("Scraping Salla store: %s (page %d)", self.store_domain, page)
            resp = self._get(url)
            if not resp:
                break
            
            page_products = self._parse_salla_page(resp.text)
            if not page_products:
                logger.debug("No products found on page %d", page)
                break
            
            products.extend(page_products)
            self._sleep()
        
        return products
    
    def _parse_salla_page(self, html: str) -> List[UnifiedProduct]:
        """تحليل صفحة متجر سلة."""
        products = self._extract_salla_api_data(html)
        if not products:
            products = self._extract_salla_html(html)
        return products
    
    def _extract_salla_api_data(self, html: str) -> List[UnifiedProduct]:
        """استخراج بيانات من API سلة المخفي في الصفحة."""
        import json
        products = []
        
        # Salla stores use window.__INITIAL_STATE__ or similar
        patterns = [
            r'window\.__INITIAL_STATE__\s*=\s*(\{.*?\});',
            r'window\.__APP_DATA__\s*=\s*(\{.*?\});',
            r'window\.__DATA__\s*=\s*(\{.*?\});',
            r'"products":\s*(\[.*?\])',
        ]
        
        for pattern in patterns:
            matches = re.findall(pattern, html, re.DOTALL)
            for match in matches:
                try:
                    data = json.loads(match)
                    items = data
                    if isinstance(data, dict):
                        # Navigate through common Salla structures
                        items = data.get("products", data.get("data", data.get("items", [])))
                    
                    if isinstance(items, list):
                        for item in items:
                            p = self._parse_salla_product(item)
                            if p:
                                products.append(p)
                except Exception as exc:
                    logger.debug("Salla API parse error: %s", exc)
            
            if products:
                break
        
        return products
    
    def _parse_salla_product(self, item: Dict) -> Optional[UnifiedProduct]:
        """تحويل بيانات منتج سلة إلى UnifiedProduct."""
        try:
            name = item.get("name", item.get("title", ""))
            if not name:
                return None
            
            price = float(item.get("price", item.get("sale_price", 0)) or 0)
            original_price = float(item.get("regular_price", item.get("old_price", price)) or price)
            
            image = ""
            if isinstance(item.get("image"), str):
                image = item["image"]
            elif isinstance(item.get("image"), dict):
                image = item["image"].get("url", item["image"].get("src", ""))
            elif isinstance(item.get("images"), list) and item["images"]:
                img = item["images"][0]
                image = img if isinstance(img, str) else img.get("url", img.get("src", ""))
            
            sku = str(item.get("sku", item.get("id", "")))
            availability = item.get("is_available", item.get("available", True))
            if isinstance(availability, str):
                availability = availability.lower() in ("true", "1", "yes", "available", "in_stock")
            
            url = ""
            if item.get("url"):
                url = item["url"] if item["url"].startswith("http") else f"{self.store_domain}{item['url']}"
            elif item.get("slug"):
                url = f"{self.store_domain}/product/{item['slug']}"
            
            return UnifiedProduct(
                name=name,
                price=price,
                original_price=original_price,
                currency="SAR",
                url=url,
                image=image,
                brand=item.get("brand", {}).get("name", "") if isinstance(item.get("brand"), dict) else str(item.get("brand", "")),
                source=f"salla_{self.store_name}",
                in_stock=availability,
                sku=sku,
                description=item.get("description", "")[:300],
                raw=item,
            )
        except Exception as exc:
            logger.debug("Parse Salla product error: %s", exc)
            return None
    
    def _extract_salla_html(self, html: str) -> List[UnifiedProduct]:
        """استخراج منتجات من HTML مباشرة (fallback)."""
        products = []
        
        # Salla product cards
        cards = re.findall(
            r'<div[^>]*class="[^"]*product[^"]*"[^>]*>(.*?)</div>\s*</div>\s*</div>',
            html, re.DOTALL | re.IGNORECASE
        )
        
        if not cards:
            # Try simpler pattern
            cards = re.findall(
                r'<div[^>]*product-card[^>]*>(.*?)</div>\s*</div>',
                html, re.DOTALL | re.IGNORECASE
            )
        
        for card in cards[:20]:
            try:
                name_match = re.search(r'<h[2-4][^>]*>([^<]+)</h[2-4]>', card, re.IGNORECASE)
                price_match = re.search(
                    r'(?:price|سعر)[^>]*>\s*(?:<[^>]+>\s*)*([\d,]+\.?\d*)',
                    card, re.IGNORECASE | re.DOTALL
                )
                img_match = re.search(r'<img[^>]*src=["\']([^"\']+)["\']', card, re.IGNORECASE)
                link_match = re.search(r'<a[^>]*href=["\']([^"\']+)["\']', card, re.IGNORECASE)
                
                if name_match and price_match:
                    name = name_match.group(1).strip()
                    price = float(price_match.group(1).replace(",", ""))
                    image = img_match.group(1) if img_match else ""
                    url = link_match.group(1) if link_match else ""
                    if url and not url.startswith("http"):
                        url = f"{self.store_domain}{url}"
                    
                    products.append(UnifiedProduct(
                        name=name,
                        price=price,
                        original_price=price * 1.1,
                        currency="SAR",
                        url=url,
                        image=image,
                        source=f"salla_{self.store_name}",
                        raw={"html_extracted": True},
                    ))
            except Exception as exc:
                logger.debug("HTML extract error: %s", exc)
        
        return products
    
    def scrape_product_detail(self, product_url: str) -> Optional[Dict[str, Any]]:
        """كشط تفاصيل منتج فردي من متجر سلة."""
        resp = self._get(product_url)
        if not resp:
            return None
        
        html = resp.text
        details = {
            "url": product_url,
            "description": "",
            "images": [],
            "variants": [],
            "specifications": {},
        }
        
        # Extract description
        desc_match = re.search(
            r'<div[^>]*(?:description|product-description|تفاصيل)[^>]*>(.*?)</div>\s*(?:<div|<section|<footer)',
            html, re.DOTALL | re.IGNORECASE
        )
        if desc_match:
            desc = re.sub(r'<[^>]+>', ' ', desc_match.group(1))
            details["description"] = re.sub(r'\s+', ' ', desc).strip()[:1000]
        
        # Extract all images
        img_matches = re.findall(r'<img[^>]*data-image=["\']([^"\']+)["\']', html, re.IGNORECASE)
        if not img_matches:
            img_matches = re.findall(r'<img[^>]*src=["\']([^"\']+)["\'][^>]*class=["\'][^"\']*product[^"\']*["\']', html, re.IGNORECASE)
        details["images"] = img_matches[:10]
        
        # Extract variants (sizes, colors)
        variant_scripts = re.findall(r'window\.__VARIANTS__\s*=\s*(\[.*?\]);', html, re.DOTALL)
        for script in variant_scripts:
            try:
                details["variants"] = json.loads(script)
            except Exception:
                pass
        
        return details


class SallaStoreDirectory:
    """دليل متاجر سلة - اكتشاف وإدارة متاجر سلة."""
    
    # بعض المتاجر المعروفة على سلة (يمكن توسيعها)
    KNOWN_PERFUME_STORES = [
        "https://www.alyasperfume.com",
        "https://www.oudhshop.com",
        "https://www.souqperfume.com",
        "https://www.azhar-oud.com",
        "https://www.alkoonperfume.com",
    ]
    
    KNOWN_ELECTRONIC_STORES = [
        "https://www.extra.com",
        "https://www.jarir.com",
    ]
    
    KNOWN_FASHION_STORES = [
        "https://www.namshi.com",
        "https://www.6thstreet.com",
    ]
    
    @classmethod
    def discover_stores(cls, category: str = "") -> List[str]:
        """اكتشاف متاجر سلة حسب الفئة."""
        if category == "perfume":
            return cls.KNOWN_PERFUME_STORES
        elif category == "electronics":
            return cls.KNOWN_ELECTRONIC_STORES
        elif category == "fashion":
            return cls.KNOWN_FASHION_STORES
        return []
    
    @classmethod
    def validate_store(cls, store_url: str) -> bool:
        """التحقق مما إذا كان الرابط متجر سلة فعلاً."""
        import requests
        try:
            resp = requests.get(store_url, timeout=10, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            })
            html = resp.text.lower()
            salla_indicators = [
                "salla", "سلة", "__salla", "salla-platform",
                "window.__INITIAL_STATE__", "salla.app",
            ]
            return any(ind in html for ind in salla_indicators)
        except Exception:
            return False


# ═══════════════════════════════════════════════════════════════════════════
#  تسجيل المحرك
# ═══════════════════════════════════════════════════════════════════════════

SCRAPER_REGISTRY["salla"] = SallaStoreScraper


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    
    # Test with a known Salla store
    test_store = "https://www.alyasperfume.com"
    print(f"Testing Salla scraper with: {test_store}")
    
    scraper = SallaStoreScraper(store_domain=test_store)
    products = scraper.scrape(pages=1)
    
    print(f"Found {len(products)} products")
    for p in products[:5]:
        print(f"  - {p.name}: {p.price} {p.currency}")
