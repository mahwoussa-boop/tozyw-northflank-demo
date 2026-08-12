"""
engines/unified_competitor_scraper.py — واجهة موحدة لكشط المنافسين
══════════════════════════════════════════════════════════════════════════════
يوفر طبقة تجريد واحدة لكشط جميع المتاجر المنافسة (Noon, Amazon, جرير, eXtra، ...).

الاستخدام:
    from engines.unified_competitor_scraper import UnifiedScraper
    
    scraper = UnifiedScraper()
    
    # كشط متجر واحد
    products = scraper.scrape("noon", category="perfumes", pages=3)
    
    # كشط جميع المتاجر
    all_data = scraper.scrape_all(targets=["noon", "amazon_sa", "jarir"])
"""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, asdict, field
from typing import Any, Dict, List, Optional, Callable
from urllib.parse import urljoin

logger = logging.getLogger("UnifiedScraper")


@dataclass
class UnifiedProduct:
    """نموذج منتج موحد لجميع المتاجر."""
    name: str = ""
    price: float = 0.0
    original_price: float = 0.0
    currency: str = "SAR"
    url: str = ""
    image: str = ""
    brand: str = ""
    category: str = ""
    sku: str = ""
    source: str = ""          # اسم المتجر: noon, amazon_sa, jarir, extra
    in_stock: bool = True
    rating: float = 0.0
    review_count: int = 0
    scraped_at: str = ""
    discount_pct: float = 0.0
    raw: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if self.raw is None:
            self.raw = {}
        if not self.scraped_at:
            self.scraped_at = time.strftime("%Y-%m-%d %H:%M:%S")
        # حساب نسبة الخصم
        if self.original_price > self.price > 0:
            object.__setattr__(self, "discount_pct", round((1 - self.price / self.original_price) * 100, 1))

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class BaseCompetitorScraper:
    """فئة أساسية لجميع محركات الكشط."""
    
    SOURCE_NAME: str = "base"
    BASE_URL: str = ""
    DEFAULT_HEADERS: Dict[str, str] = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "ar-SA,ar;q=0.9,en;q=0.8",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    
    def __init__(self, delay: float = 1.0, proxy: str = ""):
        import requests
        self.session = requests.Session()
        self.session.headers.update(self.DEFAULT_HEADERS)
        self.delay = delay
        self.proxy = {"http": proxy, "https": proxy} if proxy else {}
        self.logger = logging.getLogger(f"{self.SOURCE_NAME}Scraper")
    
    def _sleep(self) -> None:
        time.sleep(self.delay)
    
    def _get(self, url: str, **kwargs) -> Any:
        """GET آمن مع إعادة المحاولة."""
        for attempt in range(3):
            try:
                resp = self.session.get(url, proxies=self.proxy, timeout=20, **kwargs)
                if resp.status_code == 200:
                    return resp
                elif resp.status_code in (429, 503):
                    wait = 2 ** (attempt + 1)
                    self.logger.warning("HTTP %d — waiting %ds", resp.status_code, wait)
                    time.sleep(wait)
                else:
                    return resp
            except Exception as exc:
                self.logger.debug("Request error (attempt %d): %s", attempt + 1, exc)
                time.sleep(2 ** attempt)
        return None
    
    def scrape(self, category: str = "", pages: int = 1, **kwargs) -> List[UnifiedProduct]:
        """يجب تجاوزها في الفئات الفرعية."""
        raise NotImplementedError


class NoonScraper(BaseCompetitorScraper):
    """كشط Noon.com السعودية."""
    
    SOURCE_NAME = "noon"
    BASE_URL = "https://www.noon.com/saudi-ar"
    
    def scrape(self, category: str = "", pages: int = 1, search: str = "") -> List[UnifiedProduct]:
        """
        كشط Noon.
        
        Args:
            category: slug الفئة (مثل: beauty/health/fragrances)
            pages: عدد الصفحات
            search: نص البحث
        """
        products = []
        
        for page in range(1, pages + 1):
            url = self._build_url(category, page, search)
            resp = self._get(url)
            if not resp:
                continue
            
            page_products = self._parse_page(resp.text)
            products.extend(page_products)
            
            self.logger.info("Noon page %d: %d products", page, len(page_products))
            self._sleep()
        
        return products
    
    def _build_url(self, category: str, page: int, search: str) -> str:
        if search:
            return f"{self.BASE_URL}/search?q={search}&page={page}"
        return f"{self.BASE_URL}/{category}/?page={page}"
    
    def _parse_page(self, html: str) -> List[UnifiedProduct]:
        """استخراج المنتجات من HTML."""
        import re, json
        products = []
        
        # البحث عن JSON-LD أو بيانات المنتجات في الـ script
        # Noon يستخدم data في window.__INITIAL_STATE__ أو JSON-LD
        scripts = re.findall(r'<script[^>]*>(.*?)</script>', html, re.DOTALL)
        
        for script in scripts:
            # البحث عن JSON-LD Product
            if '"@type":"Product"' in script or '"@type": "Product"' in script:
                try:
                    data = json.loads(script)
                    if isinstance(data, dict) and data.get("@type") == "Product":
                        products.append(self._extract_jsonld_product(data))
                except Exception:
                    pass
            
            # Noon specific data structures
            if 'window.__INITIAL_STATE__' in script:
                try:
                    match = re.search(r'window\.__INITIAL_STATE__\s*=\s*(\{.*?\});', script, re.DOTALL)
                    if match:
                        state = json.loads(match.group(1))
                        products.extend(self._extract_noon_state(state))
                except Exception:
                    pass
        
        # Fallback: regex للأسعار في HTML
        if not products:
            products = self._extract_regex_fallback(html)
        
        return products
    
    def _extract_jsonld_product(self, data: Dict) -> UnifiedProduct:
        """استخراج من JSON-LD."""
        offers = data.get("offers", {})
        if isinstance(offers, list):
            offers = offers[0] if offers else {}
        
        price = 0.0
        try:
            price_str = str(offers.get("price", "0")).replace(",", "")
            price = float(price_str)
        except Exception:
            pass
        
        original_price = price
        if "priceCurrency" in offers:
            currency = offers["priceCurrency"]
        else:
            currency = "SAR"
        
        return UnifiedProduct(
            name=data.get("name", ""),
            price=price,
            original_price=original_price,
            currency=currency,
            url=data.get("url", ""),
            image=(data.get("image", {}).get("url", "") if isinstance(data.get("image"), dict) else data.get("image", "")),
            brand=data.get("brand", {}).get("name", "") if isinstance(data.get("brand"), dict) else str(data.get("brand", "")),
            source=self.SOURCE_NAME,
            rating=float(data.get("aggregateRating", {}).get("ratingValue", 0)),
            review_count=int(data.get("aggregateRating", {}).get("reviewCount", 0)),
        )
    
    def _extract_noon_state(self, state: Dict) -> List[UnifiedProduct]:
        """استخراج من Noon initial state."""
        products = []
        # بنية Noon متغيرة — نحاول عدة مسارات
        for key in ["products", "catalog", "searchResults", "entities"]:
            items = state.get(key, {})
            if isinstance(items, dict):
                items = items.get("products", items.get("items", items.get("data", [])))
            if isinstance(items, list):
                for item in items:
                    p = self._parse_noon_item(item)
                    if p and p.price > 0:
                        products.append(p)
        return products
    
    def _parse_noon_item(self, item: Dict) -> Optional[UnifiedProduct]:
        """تحويل عنصر Noon إلى منتج موحد."""
        try:
            name = item.get("name", item.get("title", ""))
            if not name:
                return None
            
            price_info = item.get("price", item.get("sale_price", {}))
            if isinstance(price_info, dict):
                price = float(price_info.get("amount", 0))
                currency = price_info.get("currency", "SAR")
            else:
                price = float(price_info) if price_info else 0
                currency = "SAR"
            
            original_price = item.get("original_price", item.get("list_price", price))
            if isinstance(original_price, dict):
                original_price = float(original_price.get("amount", price))
            
            return UnifiedProduct(
                name=name,
                price=price,
                original_price=original_price if original_price > price else price * 1.2,
                currency=currency,
                url=f"https://www.noon.com/saudi-ar/{item.get('url_key', item.get('sku', ''))}/p",
                image=(item.get("image", {}).get("url", "") if isinstance(item.get("image"), dict) else item.get("image", item.get("image_url", ""))),
                brand=item.get("brand", ""),
                category=item.get("category", ""),
                sku=item.get("sku", item.get("id", "")),
                source=self.SOURCE_NAME,
                in_stock=item.get("in_stock", item.get("is_available", True)),
                rating=float(item.get("rating", 0)),
                review_count=int(item.get("review_count", 0)),
                raw=item,
            )
        except Exception as exc:
            self.logger.debug("Parse noon item error: %s", exc)
            return None
    
    def _extract_regex_fallback(self, html: str) -> List[UnifiedProduct]:
        """استخراج بالـ regex fallback."""
        import re
        products = []
        
        # Noon price patterns
        price_pattern = re.compile(
            r'["\']?(?:price|salePrice)["\']?\s*[:=]\s*["\']?(\d+(?:\.\d+)?)["\']?',
            re.IGNORECASE,
        )
        name_pattern = re.compile(
            r'["\']?(?:name|title)["\']?\s*[:=]\s*["\']([^"\']+)["\']',
            re.IGNORECASE,
        )
        
        prices = price_pattern.findall(html)
        names = name_pattern.findall(html)
        
        for i, price_str in enumerate(prices[:20]):  # max 20
            try:
                price = float(price_str)
                name = names[i] if i < len(names) else f"Noon Product {i+1}"
                products.append(UnifiedProduct(
                    name=name,
                    price=price,
                    original_price=price * 1.2,
                    source=self.SOURCE_NAME,
                ))
            except Exception:
                pass
        
        return products


class AmazonSAScraper(BaseCompetitorScraper):
    """كشط Amazon.sa السعودية."""
    
    SOURCE_NAME = "amazon_sa"
    BASE_URL = "https://www.amazon.sa"
    
    def scrape(self, category: str = "", pages: int = 1, search: str = "") -> List[UnifiedProduct]:
        products = []
        
        for page in range(1, pages + 1):
            url = self._build_url(category, page, search)
            resp = self._get(url)
            if not resp:
                continue
            
            page_products = self._parse_page(resp.text)
            products.extend(page_products)
            
            self.logger.info("Amazon SA page %d: %d products", page, len(page_products))
            self._sleep()
        
        return products
    
    def _build_url(self, category: str, page: int, search: str) -> str:
        if search:
            return f"{self.BASE_URL}/s?k={search}&page={page}"
        # Amazon category nodes
        cat_nodes = {
            "beauty": "1249",
            "perfumes": "",
            "electronics": "",
        }
        node = cat_nodes.get(category, "")
        if node:
            return f"{self.BASE_URL}/s?rh=n%3A{node}&page={page}"
        return f"{self.BASE_URL}/s?k={category}&page={page}"
    
    def _parse_page(self, html: str) -> List[UnifiedProduct]:
        """استخراج منتجات Amazon."""
        import re
        products = []
        
        # Amazon يستخدم JSON في window.AmazonJSON or data attributes
        # استخراج ASIN blocks
        asin_blocks = re.findall(
            r'data-asin="([A-Z0-9]{10})"[^>]*>(.*?)</div>(?=[^<]*</div>|\s*$)',
            html, re.DOTALL,
        )
        
        if not asin_blocks:
            # Fallback: أي ASIN مع نص
            asin_blocks = re.findall(
                r'data-asin="([A-Z0-9]{10})"[^>]*>.*?<span[^>]*>([^<]+)</span>',
                html, re.DOTALL,
            )
        
        for asin, block in asin_blocks[:50]:
            p = self._parse_asin_block(asin, block)
            if p:
                products.append(p)
        
        # JSON-LD fallback
        if not products:
            products = self._extract_jsonld(html)
        
        return products
    
    def _parse_asin_block(self, asin: str, block: str) -> Optional[UnifiedProduct]:
        """تحليل بلوك ASIN واحد."""
        import re
        try:
            # Name
            name_match = re.search(r'<span[^>]*>([^<]{10,200})</span>', block)
            name = name_match.group(1).strip() if name_match else ""
            
            # Price
            price = 0.0
            price_match = re.search(r'(?:\$|SAR|ر\.س)\s*([\d,]+\.?\d*)', block)
            if price_match:
                price = float(price_match.group(1).replace(",", ""))
            else:
                # Try data-price
                dp = re.search(r'data-price="([\d.]+)"', block)
                if dp:
                    price = float(dp.group(1))
            
            # Image
            img = re.search(r'src="(https://m\.media-amazon\.com/images/[^"]+)"', block)
            image = img.group(1) if img else ""
            
            # Rating
            rating_match = re.search(r'([\d.]+)\s*out of\s*5', block)
            rating = float(rating_match.group(1)) if rating_match else 0.0
            
            if not name or price <= 0:
                return None
            
            return UnifiedProduct(
                name=name,
                price=price,
                original_price=price * 1.15,  # Amazon يعرض غالباً سعر خاص
                currency="SAR",
                url=f"{self.BASE_URL}/dp/{asin}",
                image=image,
                source=self.SOURCE_NAME,
                sku=asin,
                rating=rating,
                raw={"asin": asin, "block": block[:500]},
            )
        except Exception as exc:
            self.logger.debug("Parse ASIN error: %s", exc)
            return None
    
    def _extract_jsonld(self, html: str) -> List[UnifiedProduct]:
        """JSON-LD fallback."""
        import re, json
        products = []
        matches = re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.DOTALL)
        
        for match in matches:
            try:
                data = json.loads(match)
                if data.get("@type") == "Product":
                    offers = data.get("offers", {})
                    if isinstance(offers, list):
                        offers = offers[0] if offers else {}
                    
                    price = 0.0
                    try:
                        price = float(offers.get("price", "0"))
                    except Exception:
                        pass
                    
                    products.append(UnifiedProduct(
                        name=data.get("name", ""),
                        price=price,
                        original_price=price,
                        currency=offers.get("priceCurrency", "SAR"),
                        url=data.get("url", ""),
                        image=(data.get("image", {}).get("url", "") if isinstance(data.get("image"), dict) else data.get("image", "")),
                        brand=data.get("brand", {}).get("name", "") if isinstance(data.get("brand"), dict) else "",
                        source=self.SOURCE_NAME,
                        rating=float(data.get("aggregateRating", {}).get("ratingValue", 0)),
                    ))
            except Exception:
                pass
        
        return products


class JarirScraper(BaseCompetitorScraper):
    """كشط مكتبة جرير."""
    
    SOURCE_NAME = "jarir"
    BASE_URL = "https://www.jarir.com"
    
    def scrape(self, category: str = "", pages: int = 1, search: str = "") -> List[UnifiedProduct]:
        products = []
        
        for page in range(1, pages + 1):
            url = self._build_url(category, page, search)
            resp = self._get(url)
            if not resp:
                continue
            
            page_products = self._parse_page(resp.text)
            products.extend(page_products)
            
            self.logger.info("Jarir page %d: %d products", page, len(page_products))
            self._sleep()
        
        return products
    
    def _build_url(self, category: str, page: int, search: str) -> str:
        if search:
            return f"{self.BASE_URL}/sa/search?text={search}&page={page}"
        return f"{self.BASE_URL}/sa/{category}.html?p={page}"
    
    def _parse_page(self, html: str) -> List[UnifiedProduct]:
        """استخراج منتجات جرير."""
        import re, json
        products = []
        
        # جرير يستخدم JSON في window.catalogData أو data attributes
        scripts = re.findall(r'<script[^>]*>(.*?)</script>', html, re.DOTALL)
        
        for script in scripts:
            if 'window.catalogData' in script or 'window.__INITIAL_STATE__' in script:
                try:
                    match = re.search(r'window\.(?:catalogData|__INITIAL_STATE__)\s*=\s*(\{.*?\});', script, re.DOTALL)
                    if match:
                        data = json.loads(match.group(1))
                        products.extend(self._extract_jarir_data(data))
                except Exception:
                    pass
        
        # Fallback: regex
        if not products:
            products = self._extract_regex_fallback(html)
        
        return products
    
    def _extract_jarir_data(self, data: Dict) -> List[UnifiedProduct]:
        """استخراج من بيانات جرير."""
        products = []
        items = data.get("products", data.get("items", data.get("data", [])))
        
        if isinstance(items, dict):
            items = items.get("items", items.get("products", []))
        
        if not isinstance(items, list):
            return products
        
        for item in items:
            try:
                name = item.get("name", item.get("product_name", ""))
                if not name:
                    continue
                
                price = 0.0
                price_data = item.get("price", item.get("final_price", {}))
                if isinstance(price_data, dict):
                    price = float(price_data.get("amount", 0))
                    currency = price_data.get("currency", "SAR")
                else:
                    price = float(price_data) if price_data else 0
                    currency = "SAR"
                
                original_price = item.get("original_price", item.get("old_price", price))
                if isinstance(original_price, dict):
                    original_price = float(original_price.get("amount", price))
                
                products.append(UnifiedProduct(
                    name=name,
                    price=price,
                    original_price=original_price if original_price > price else price * 1.1,
                    currency=currency,
                    url=urljoin(self.BASE_URL, item.get('url', item.get('product_url', ''))),
                    image=(item.get("image", {}).get("url", "") if isinstance(item.get("image"), dict) else item.get("image", item.get("product_image", ""))),
                    brand=item.get("brand", ""),
                    category=item.get("category", ""),
                    sku=item.get("sku", item.get("id", "")),
                    source=self.SOURCE_NAME,
                    in_stock=item.get("in_stock", item.get("is_available", True)),
                    rating=float(item.get("rating", 0)),
                    raw=item,
                ))
            except Exception as exc:
                self.logger.debug("Parse jarir item error: %s", exc)
        
        return products
    
    def _extract_regex_fallback(self, html: str) -> List[UnifiedProduct]:
        """Regex fallback."""
        import re
        products = []
        
        # جرير product cards
        cards = re.findall(
            r'<div[^>]*class="[^"]*product[^"]*"[^>]*>(.*?)</div>\s*</div>',
            html, re.DOTALL,
        )
        
        for card in cards:
            name_match = re.search(r'<h[23][^>]*>([^<]+)</h', card)
            price_match = re.search(r'([\d,]+\.?\d*)\s*(?:SAR|ر\.س)', card)
            
            if name_match and price_match:
                try:
                    price = float(price_match.group(1).replace(",", ""))
                    products.append(UnifiedProduct(
                        name=name_match.group(1).strip(),
                        price=price,
                        original_price=price * 1.1,
                        source=self.SOURCE_NAME,
                    ))
                except Exception:
                    pass
        
        return products


class ExtraScraper(BaseCompetitorScraper):
    """كشط eXtra.com السعودية."""
    
    SOURCE_NAME = "extra"
    BASE_URL = "https://www.extra.com"
    
    def scrape(self, category: str = "", pages: int = 1, search: str = "") -> List[UnifiedProduct]:
        products = []
        
        for page in range(1, pages + 1):
            url = self._build_url(category, page, search)
            resp = self._get(url)
            if not resp:
                continue
            
            page_products = self._parse_page(resp.text)
            products.extend(page_products)
            
            self.logger.info("Extra page %d: %d products", page, len(page_products))
            self._sleep()
        
        return products
    
    def _build_url(self, category: str, page: int, search: str) -> str:
        if search:
            return f"{self.BASE_URL}/ar-sa/search/?q={search}&page={page}"
        return f"{self.BASE_URL}/ar-sa/{category}.html?p={page}"
    
    def _parse_page(self, html: str) -> List[UnifiedProduct]:
        """استخراج منتجات eXtra."""
        import re, json
        products = []
        
        # eXtra يستخدم JSON-LD و window.__INITIAL_STATE__
        scripts = re.findall(r'<script[^>]*>(.*?)</script>', html, re.DOTALL)
        
        for script in scripts:
            if 'window.__INITIAL_STATE__' in script or 'window.catalog' in script:
                try:
                    match = re.search(r'window\.(?:__INITIAL_STATE__|catalog)\s*=\s*(\{.*?\});', script, re.DOTALL)
                    if match:
                        data = json.loads(match.group(1))
                        products.extend(self._extract_extra_data(data))
                except Exception:
                    pass
        
        # JSON-LD
        if not products:
            products = self._extract_jsonld(html)
        
        return products
    
    def _extract_extra_data(self, data: Dict) -> List[UnifiedProduct]:
        """استخراج من بيانات eXtra."""
        products = []
        items = data.get("products", data.get("items", data.get("catalog", {}).get("items", [])))
        
        if not isinstance(items, list):
            return products
        
        for item in items:
            try:
                name = item.get("name", item.get("product_name", ""))
                if not name:
                    continue
                
                price = float(item.get("price", item.get("final_price", 0)))
                original_price = float(item.get("original_price", item.get("old_price", price)))
                
                products.append(UnifiedProduct(
                    name=name,
                    price=price,
                    original_price=original_price if original_price > price else price * 1.15,
                    currency="SAR",
                    url=urljoin(self.BASE_URL, item.get('url', item.get('product_url', ''))),
                    image=(item.get("image", {}).get("url", "") if isinstance(item.get("image"), dict) else item.get("image", item.get("product_image", ""))),
                    brand=item.get("brand", ""),
                    category=item.get("category", ""),
                    sku=item.get("sku", item.get("id", "")),
                    source=self.SOURCE_NAME,
                    in_stock=item.get("in_stock", item.get("is_available", True)),
                    rating=float(item.get("rating", 0)),
                    raw=item,
                ))
            except Exception as exc:
                self.logger.debug("Parse extra item error: %s", exc)
        
        return products
    
    def _extract_jsonld(self, html: str) -> List[UnifiedProduct]:
        """JSON-LD fallback."""
        import re, json
        products = []
        matches = re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.DOTALL)
        
        for match in matches:
            try:
                data = json.loads(match)
                if data.get("@type") == "Product":
                    offers = data.get("offers", {})
                    if isinstance(offers, list):
                        offers = offers[0] if offers else {}
                    
                    price = 0.0
                    try:
                        price = float(offers.get("price", "0"))
                    except Exception:
                        pass
                    
                    products.append(UnifiedProduct(
                        name=data.get("name", ""),
                        price=price,
                        original_price=price,
                        currency=offers.get("priceCurrency", "SAR"),
                        url=data.get("url", ""),
                        image=(data.get("image", {}).get("url", "") if isinstance(data.get("image"), dict) else data.get("image", "")),
                        brand=data.get("brand", {}).get("name", "") if isinstance(data.get("brand"), dict) else "",
                        source=self.SOURCE_NAME,
                        rating=float(data.get("aggregateRating", {}).get("ratingValue", 0)),
                    ))
            except Exception:
                pass
        
        return products


# ═══════════════════════════════════════════════════════════════════════════
#  واجهة التحكم الموحدة
# ═══════════════════════════════════════════════════════════════════════════

SCRAPER_REGISTRY = {
    "noon": NoonScraper,
    "amazon_sa": AmazonSAScraper,
    "jarir": JarirScraper,
    "extra": ExtraScraper,
    "mahally": None,  # يُحمّل ديناميكياً من mahally_scraper
}


class UnifiedScraper:
    """واجهة موحدة لكشط جميع المتاجر المنافسة."""
    
    def __init__(self, delay: float = 1.0, proxy: str = ""):
        self.delay = delay
        self.proxy = proxy
        self._scrapers: Dict[str, BaseCompetitorScraper] = {}
        self.logger = logging.getLogger("UnifiedScraper")
    
    def _get_scraper(self, source: str) -> BaseCompetitorScraper:
        """الحصول على محرك الكشط أو إنشاؤه."""
        if source not in self._scrapers:
            scraper_cls = SCRAPER_REGISTRY.get(source)
            if scraper_cls is None:
                if source == "mahally":
                    # تحميل ديناميكي
                    try:
                        from engines.mahally_scraper import MahallyScraper
                        self._scrapers[source] = MahallyScraper()  # type: ignore
                        return self._scrapers[source]
                    except Exception as exc:
                        raise ValueError(f"Cannot load MahallyScraper: {exc}")
                raise ValueError(f"Unknown scraper source: {source}. Available: {list(SCRAPER_REGISTRY.keys())}")
            self._scrapers[source] = scraper_cls(delay=self.delay, proxy=self.proxy)
        return self._scrapers[source]
    
    def scrape(
        self,
        source: str,
        category: str = "",
        pages: int = 1,
        search: str = "",
        **kwargs,
    ) -> List[UnifiedProduct]:
        """
        كشط متجر واحد.
        
        Args:
            source: اسم المتجر (noon, amazon_sa, jarir, extra, mahally)
            category: فئة المنتجات
            pages: عدد الصفحات للكشط
            search: نص البحث (بديل عن الفئة)
        
        Returns:
            قائمة منتجات موحدة
        """
        scraper = self._get_scraper(source)
        self.logger.info("Scraping %s (category=%s, pages=%d, search=%s)", source, category, pages, search)
        
        start = time.time()
        products = scraper.scrape(category=category, pages=pages, search=search, **kwargs)
        elapsed = time.time() - start
        
        self.logger.info("Scraped %d products from %s in %.1fs", len(products), source, elapsed)
        return products
    
    def scrape_all(
        self,
        targets: List[str],
        category: str = "",
        pages: int = 1,
        search: str = "",
        max_workers: int = 3,
        progress_callback: Optional[Callable[[str, int, int], None]] = None,
    ) -> Dict[str, List[UnifiedProduct]]:
        """
        كشط متعدد بالتوازي.
        
        Args:
            targets: قائمة أسماء المتاجر
            category: الفئة
            pages: عدد الصفحات لكل متجر
            search: نص البحث
            max_workers: عدد الخيوط المتوازية
            progress_callback: دالة callback (source, done, total)
        
        Returns:
            {source: [products]}
        """
        results: Dict[str, List[UnifiedProduct]] = {}
        total = len(targets)
        
        self.logger.info("Parallel scraping %d stores (max_workers=%d)", total, max_workers)
        
        def _scrape_one(source: str) -> tuple[str, List[UnifiedProduct]]:
            try:
                products = self.scrape(source, category=category, pages=pages, search=search)
                return source, products
            except Exception as exc:
                self.logger.error("Failed to scrape %s: %s", source, exc)
                return source, []
        
        workers = min(max_workers, total, 5)
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(_scrape_one, t): t for t in targets}
            completed = 0
            
            for future in as_completed(futures):
                source, products = future.result()
                results[source] = products
                completed += 1
                
                if progress_callback:
                    progress_callback(source, completed, total)
        
        total_products = sum(len(v) for v in results.values())
        self.logger.info("Parallel scraping complete: %d stores, %d products", len(results), total_products)
        return results
    
    def compare_prices(
        self,
        product_name: str,
        sources: List[str],
    ) -> Dict[str, Any]:
        """
        مقارنة أسعار منتج محدد عدة متاجر.
        
        Args:
            product_name: اسم المنتج للبحث
            sources: قائمة المتاجر
        
        Returns:
            تحليل مقارنة شامل
        """
        all_results = self.scrape_all(targets=sources, search=product_name, pages=1)
        
        matches: Dict[str, List[UnifiedProduct]] = {}
        for source, products in all_results.items():
            # تصفية المنتجات المطابقة
            name_lower = product_name.lower()
            matched = [p for p in products if name_lower in p.name.lower()]
            matches[source] = matched
        
        # تحليل
        analysis = {
            "query": product_name,
            "sources": {},
            "cheapest": None,
            "most_expensive": None,
            "average": 0.0,
        }
        
        all_prices = []
        for source, products in matches.items():
            if products:
                prices = [p.price for p in products if p.price > 0]
                if prices:
                    avg = sum(prices) / len(prices)
                    min_p = min(prices)
                    max_p = max(prices)
                    all_prices.extend(prices)
                    
                    analysis["sources"][source] = {
                        "count": len(products),
                        "average": round(avg, 2),
                        "min": min_p,
                        "max": max_p,
                        "products": [p.to_dict() for p in products[:5]],  # top 5
                    }
        
        if all_prices:
            analysis["average"] = round(sum(all_prices) / len(all_prices), 2)
            analysis["cheapest"] = min(all_prices)
            analysis["most_expensive"] = max(all_prices)
        
        return analysis
    
    def register_scraper(self, name: str, scraper_class: type) -> None:
        """تسجيل محرك كشط مخصص."""
        SCRAPER_REGISTRY[name] = scraper_class
        self.logger.info("Registered scraper: %s", name)
    
    def list_sources(self) -> List[str]:
        """قائمة المتاجر المدعومة."""
        return list(SCRAPER_REGISTRY.keys())


# ═══════════════════════════════════════════════════════════════════════════
#  تصدير
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # اختبار سريع
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    
    scraper = UnifiedScraper(delay=0.5)
    print("Available sources:", scraper.list_sources())
    
    # اختبار كشط Noon (فئة عطور)
    # products = scraper.scrape("noon", category="beauty/health/fragrances", pages=1)
    # print(f"Noon products: {len(products)}")
    # for p in products[:3]:
    #     print(f"  - {p.name}: {p.price} {p.currency}")
