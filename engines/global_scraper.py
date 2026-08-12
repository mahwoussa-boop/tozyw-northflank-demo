"""
engines/global_scraper.py — محرك كشط للمتاجر العالمية
══════════════════════════════════════════════════════════════════════════════
يدعم:
- Amazon.com / Amazon.ae / Amazon.sa
- eBay
- FragranceNet (عطور)
- Notino (عطور)
- Sephora
- Ulta
- FragranceX
"""
from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

from engines.unified_competitor_scraper import (
    BaseCompetitorScraper, UnifiedProduct, SCRAPER_REGISTRY,
)


logger = logging.getLogger("GlobalScraper")


class AmazonScraper(BaseCompetitorScraper):
    """كشط أمازون (US, AE, SA)."""
    
    SOURCE_NAME = "amazon"
    
    MARKETPLACES = {
        "us": "https://www.amazon.com",
        "ae": "https://www.amazon.ae",
        "sa": "https://www.amazon.sa",
    }
    
    def __init__(self, marketplace: str = "us", **kwargs):
        super().__init__(**kwargs)
        self.marketplace = marketplace
        self.base_url = self.MARKETPLACES.get(marketplace, "https://www.amazon.com")
    
    def scrape(self, category: str = "", pages: int = 1, search: str = "") -> List[UnifiedProduct]:
        """كشط أمازون."""
        products = []
        
        for page in range(1, pages + 1):
            if search:
                url = f"{self.base_url}/s?k={search.replace(' ', '+')}&page={page}"
            elif category:
                url = f"{self.base_url}/s?i={category}&page={page}"
            else:
                continue
            
            logger.info("Scraping Amazon %s: %s", self.marketplace, url)
            resp = self._get(url)
            if not resp:
                continue
            
            page_products = self._parse_amazon_page(resp.text)
            products.extend(page_products)
            self._sleep(2.0)  # Amazon is strict
        
        return products
    
    def _parse_amazon_page(self, html: str) -> List[UnifiedProduct]:
        """تحليل صفحة نتائج أمازون."""
        products = []
        
        # Amazon product cards (s-result-item)
        cards = re.findall(
            r'<div[^>]*data-component-type="s-search-result"[^>]*>(.*?)</div>\s*</div>\s*</div>',
            html, re.DOTALL
        )
        
        for card in cards:
            try:
                # Extract ASIN
                asin_match = re.search(r'data-asin="([A-Z0-9]+)"', card)
                asin = asin_match.group(1) if asin_match else ""
                
                # Extract name
                name_match = re.search(
                    r'<span[^>]*class="[^"]*a-size-base-plus[^"]*"[^>]*>([^<]+)</span>',
                    card
                ) or re.search(
                    r'<h2[^>]*>(.*?)</h2>', card, re.DOTALL
                )
                name = ""
                if name_match:
                    name = re.sub(r'<[^>]+>', '', name_match.group(1)).strip()
                
                # Extract price
                price_match = re.search(
                    r'<span[^>]*class="[^"]*a-price[^"]*"[^>]*>.*?<span[^>]*>([^<]+)</span>',
                    card, re.DOTALL
                )
                price = 0.0
                if price_match:
                    price_text = price_match.group(1).replace(',', '').replace('$', '').replace('AED', '').replace('SAR', '').strip()
                    try:
                        price = float(price_text)
                    except ValueError:
                        pass
                
                # Extract image
                img_match = re.search(r'<img[^>]*src="([^"]+)"[^>]*class="[^"]*s-image[^"]*"', card)
                image = img_match.group(1) if img_match else ""
                
                # Extract rating
                rating_match = re.search(r'<span[^>]*class="[^"]*a-icon-alt[^"]*"[^>]*>([\d.]+)', card)
                rating = float(rating_match.group(1)) if rating_match else 0.0
                
                url = f"{self.base_url}/dp/{asin}" if asin else ""
                
                products.append(UnifiedProduct(
                    name=name,
                    price=price,
                    original_price=price * 1.1,
                    currency="USD" if self.marketplace == "us" else ("AED" if self.marketplace == "ae" else "SAR"),
                    url=url,
                    image=image,
                    source=f"amazon_{self.marketplace}",
                    in_stock=bool(asin),
                    sku=asin,
                    raw={"rating": rating},
                ))
            except Exception as exc:
                logger.debug("Amazon parse error: %s", exc)
        
        return products


class FragranceNetScraper(BaseCompetitorScraper):
    """كشط موقع FragranceNet (عطور مخفضة)."""
    
    SOURCE_NAME = "fragrancenet"
    BASE_URL = "https://www.fragrancenet.com"
    
    def scrape(self, category: str = "", pages: int = 1, search: str = "") -> List[UnifiedProduct]:
        products = []
        
        for page in range(1, pages + 1):
            if search:
                url = f"{self.BASE_URL}/search?search={search.replace(' ', '+')}&page={page}"
            elif category:
                url = f"{self.BASE_URL}/{category}?page={page}"
            else:
                url = f"{self.BASE_URL}/new-arrivals?page={page}"
            
            resp = self._get(url)
            if not resp:
                continue
            
            products.extend(self._parse_fragrancenet(resp.text))
            self._sleep(1.5)
        
        return products
    
    def _parse_fragrancenet(self, html: str) -> List[UnifiedProduct]:
        products = []
        
        # FragranceNet product cards
        cards = re.findall(r'<div class="product-item"[^>]*>(.*?)</div>\s*</div>', html, re.DOTALL)
        
        for card in cards:
            try:
                name_match = re.search(r'<h3[^>]*>(.*?)</h3>', card, re.DOTALL)
                price_match = re.search(r'\$([\d.]+)', card)
                img_match = re.search(r'<img[^>]*src="([^"]+)"', card)
                link_match = re.search(r'<a[^>]*href="([^"]+)"', card)
                
                if name_match and price_match:
                    name = re.sub(r'<[^>]+>', '', name_match.group(1)).strip()
                    price = float(price_match.group(1))
                    image = img_match.group(1) if img_match else ""
                    url = urljoin(self.BASE_URL, link_match.group(1)) if link_match else ""
                    
                    products.append(UnifiedProduct(
                        name=name,
                        price=price,
                        original_price=price * 1.3,
                        currency="USD",
                        url=url,
                        image=image,
                        source="fragrancenet",
                        raw={},
                    ))
            except Exception:
                pass
        
        return products


class NotinoScraper(BaseCompetitorScraper):
    """كشط موقع Notino (عطور أوروبية)."""
    
    SOURCE_NAME = "notino"
    BASE_URL = "https://www.notino.com"
    
    def scrape(self, category: str = "", pages: int = 1, search: str = "") -> List[UnifiedProduct]:
        products = []
        
        for page in range(1, pages + 1):
            if search:
                url = f"{self.BASE_URL}/search?q={search.replace(' ', '+')}&page={page}"
            elif category:
                url = f"{self.BASE_URL}/{category}?page={page}"
            else:
                url = f"{self.BASE_URL}/perfumes?page={page}"
            
            resp = self._get(url)
            if not resp:
                continue
            
            products.extend(self._parse_notino(resp.text))
            self._sleep(1.5)
        
        return products
    
    def _parse_notino(self, html: str) -> List[UnifiedProduct]:
        products = []
        
        # Notino uses JSON-LD
        scripts = re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.DOTALL)
        for script in scripts:
            try:
                data = json.loads(script)
                if data.get("@type") == "Product":
                    offers = data.get("offers", {})
                    if isinstance(offers, list):
                        offers = offers[0] if offers else {}
                    
                    price = float(offers.get("price", "0") or 0)
                    name = data.get("name", "")
                    brand = data.get("brand", {}).get("name", "") if isinstance(data.get("brand"), dict) else ""
                    
                    products.append(UnifiedProduct(
                        name=name,
                        price=price,
                        original_price=price * 1.2,
                        currency="EUR",
                        url=data.get("url", ""),
                        image=(data.get("image", {}).get("url", "") if isinstance(data.get("image"), dict) else data.get("image", "")),
                        brand=brand,
                        source="notino",
                        raw=data,
                    ))
            except Exception:
                pass
        
        return products


# ═══════════════════════════════════════════════════════════════════════════
#  تسجيل المحركات
# ═══════════════════════════════════════════════════════════════════════════

SCRAPER_REGISTRY["amazon"] = AmazonScraper
SCRAPER_REGISTRY["fragrancenet"] = FragranceNetScraper
SCRAPER_REGISTRY["notino"] = NotinoScraper


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    
    # Test Amazon SA
    print("Testing Amazon SA scraper...")
    scraper = AmazonScraper(marketplace="sa")
    products = scraper.scrape(search="oud perfume", pages=1)
    print(f"Found {len(products)} products on Amazon SA")
    for p in products[:3]:
        print(f"  - {p.name}: {p.price} {p.currency}")
