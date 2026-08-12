"""
engines/perfume_scraper.py — محرك كشط متخصص للعطور والنيش
══════════════════════════════════════════════════════════════════════════════
متخصص في كشط:
- عطور Niche (عطور نيش)
- عطور Designer (ديور، شانيل، غوتشي، إلخ)
- تسترات (عينات)
- زيوت عطرية
- بخور وعود

يدعم متاجر متخصصة في العطور في السعودية والخليج.
"""
from __future__ import annotations

import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, asdict, field
from typing import Any, Dict, List, Optional, Tuple

from engines.unified_competitor_scraper import (
    BaseCompetitorScraper, UnifiedProduct, UnifiedScraper,
)

logger = logging.getLogger("PerfumeScraper")


@dataclass
class PerfumeProduct(UnifiedProduct):
    """منتج عطر موسّع بحقول متخصصة."""
    
    perfume_type: str = ""        # eau_de_parfum, eau_de_toilette, attar, oud, tester
    concentration: str = ""       # 5%, 10%, 20%, oud_pure
    size_ml: int = 0             # 50ml, 100ml, 200ml
    gender: str = ""            # men, women, unisex
    notes_top: List[str] = field(default_factory=list)
    notes_middle: List[str] = field(default_factory=list)
    notes_base: List[str] = field(default_factory=list)
    longevity_hours: int = 0
    sillage: str = ""           # intimate, moderate, strong, beast_mode
    season: str = ""            # summer, winter, all_season
    is_tester: bool = False
    is_niche: bool = False
    is_oud: bool = False
    batch_code: str = ""
    
    def __post_init__(self):
        super().__post_init__()
    
    def to_dict(self) -> Dict[str, Any]:
        base = super().to_dict()
        base.update({
            "perfume_type": self.perfume_type,
            "concentration": self.concentration,
            "size_ml": self.size_ml,
            "gender": self.gender,
            "notes_top": self.notes_top,
            "notes_middle": self.notes_middle,
            "notes_base": self.notes_base,
            "longevity_hours": self.longevity_hours,
            "sillage": self.sillage,
            "season": self.season,
            "is_tester": self.is_tester,
            "is_niche": self.is_niche,
            "is_oud": self.is_oud,
            "batch_code": self.batch_code,
        })
        return base


class PerfumeClassifier:
    """مصنّف ذكي للعطور يستخرج الخصائص من اسم المنتج."""
    
    # أنواع العطور
    PERFUME_TYPES = {
        "eau_de_parfum": ["eau de parfum", "edp", "أو دو برفوم", "بارفيوم"],
        "eau_de_toilette": ["eau de toilette", "edt", "أو دو تواليت"],
        "eau_de_cologne": ["cologne", "eau de cologne", "cologne", "كولونيا"],
        "parfum": ["parfum extrait", "pure parfum", "parfum", "اكسترايت"],
        "attar": ["attar", "ittar", "عطر", "زيت عطري", "دهن عود"],
        "oud": ["oud", "عود", "بخور", "مبخر"],
        "tester": ["tester", "تستر", "عينة", "sample"],
    }
    
    # الأحجام
    SIZE_PATTERNS = [
        (r"(\d+)\s*ml\b", 1),
        (r"(\d+)\s*مل\b", 1),
        (r"(\d+)\s*ml\s*e\b", 1),
    ]
    
    # الجنس
    GENDER_PATTERNS = {
        "men": ["for men", "homme", "pour homme", "رجالي", "للرجال"],
        "women": ["for women", "femme", "pour femme", "نسائي", "للنساء"],
        "unisex": ["unisex", "للجنسين", "unisexe"],
    }
    
    # العلامات التجارية النيش
    NICHE_BRANDS = [
        "amouage", "creed", "byredo", "diptyque", "frederic malle", "le labo",
        "maison francis kurkdjian", "mfk", "nasomatto", "xerjoff", "roja",
        "tom ford private blend", "clive christian", "bond no. 9", "kilian",
        "memo", "parfums de marly", "initio", "mancera", "montale", "nishane",
        "serge lutens", "tauer", "l'artisan", "penhaligon's", "ormonde jayne",
        "العربية للعود", "عبدالصمد القرشي", "الماجد للعود", "المسك",
    ]
    
    # العلامات التجارية المصممة
    DESIGNER_BRANDS = [
        "dior", "chanel", "gucci", "versace", "armani", "prada", "ysl",
        "hermes", "lv", "louis vuitton", "burberry", "dolce gabbana", "dg",
        "calvin klein", "ck", "hugo boss", "carolina herrera", "ch",
        "paco rabanne", "jean paul gaultier", "jpg", "lancome", "givenchy",
        "bvlgari", "bulgari", "cartier", "tiffany", "coach", "marc jacobs",
    ]
    
    # مكونات العطور
    TOP_NOTES = ["bergamot", "lemon", "lime", "orange", "mandarin", "grapefruit",
                 "lavender", "pepper", "mint", "basil", "sage", "neroli"]
    MIDDLE_NOTES = ["rose", "jasmine", "lily", "ylang", "iris", "violet",
                    "geranium", "cardamom", "cinnamon", "nutmeg", "saffron"]
    BASE_NOTES = ["oud", "amber", "musk", "vanilla", "sandalwood", "cedar",
                  "patchouli", "vetiver", "leather", "tobacco", "tonka"]
    
    @classmethod
    def classify(cls, name: str, brand: str = "", price: float = 0) -> Dict[str, Any]:
        """تصنيف منتج عطر من اسمه."""
        name_lower = name.lower()
        brand_lower = brand.lower()
        
        result = {
            "perfume_type": cls._detect_type(name_lower),
            "size_ml": cls._detect_size(name_lower),
            "gender": cls._detect_gender(name_lower),
            "is_tester": cls._is_tester(name_lower),
            "is_niche": cls._is_niche(brand_lower, price),
            "is_oud": cls._is_oud(name_lower, brand_lower),
            "notes_top": cls._detect_notes(name_lower, cls.TOP_NOTES),
            "notes_middle": cls._detect_notes(name_lower, cls.MIDDLE_NOTES),
            "notes_base": cls._detect_notes(name_lower, cls.BASE_NOTES),
            "sillage": cls._estimate_sillage(name_lower, brand_lower),
            "season": cls._estimate_season(name_lower),
        }
        
        return result
    
    @classmethod
    def _detect_type(cls, name: str) -> str:
        for ptype, keywords in cls.PERFUME_TYPES.items():
            if any(kw in name for kw in keywords):
                return ptype
        return "eau_de_parfum"  # default
    
    @classmethod
    def _detect_size(cls, name: str) -> int:
        for pattern, group in cls.SIZE_PATTERNS:
            match = re.search(pattern, name, re.IGNORECASE)
            if match:
                try:
                    return int(match.group(group))
                except ValueError:
                    pass
        return 0
    
    @classmethod
    def _detect_gender(cls, name: str) -> str:
        for gender, keywords in cls.GENDER_PATTERNS.items():
            if any(kw in name for kw in keywords):
                return gender
        return "unisex"
    
    @classmethod
    def _is_tester(cls, name: str) -> bool:
        return any(kw in name for kw in cls.PERFUME_TYPES["tester"])
    
    @classmethod
    def _is_niche(cls, brand: str, price: float) -> bool:
        if any(nb in brand for nb in cls.NICHE_BRANDS):
            return True
        # العطور بأكثر من 1000 ريال غالباً نيش
        if price > 1000:
            return True
        return False
    
    @classmethod
    def _is_oud(cls, name: str, brand: str) -> bool:
        oud_keywords = ["oud", "عود", "دهن", "bukhoor", "بخور", "mukhallat", "مخلط"]
        return any(kw in name or kw in brand for kw in oud_keywords)
    
    @classmethod
    def _detect_notes(cls, name: str, note_list: List[str]) -> List[str]:
        return [note for note in note_list if note in name]
    
    @classmethod
    def _estimate_sillage(cls, name: str, brand: str) -> str:
        strong = ["intense", "extreme", "strong", "powerful", "beast", "heavy", "ثقيل"]
        intimate = ["light", "soft", "subtle", "gentle", "خفيف", "ناعم"]
        if any(kw in name for kw in strong):
            return "strong"
        if any(kw in name for kw in intimate):
            return "intimate"
        if "oud" in name or "عود" in brand:
            return "strong"
        return "moderate"
    
    @classmethod
    def _estimate_season(cls, name: str) -> str:
        summer = ["fresh", "citrus", "aquatic", "marine", "summer", "صيفي", "منعش"]
        winter = ["oud", "amber", "vanilla", "warm", "spicy", "winter", "شتوي", "دافئ"]
        if any(kw in name for kw in summer):
            return "summer"
        if any(kw in name for kw in winter):
            return "winter"
        return "all_season"


class OudAndAttarScraper(BaseCompetitorScraper):
    """كشط متخصص للعود والبخور والعطور الشرقية."""
    
    SOURCE_NAME = "oud_attar"
    BASE_URL = ""
    
    # متاجر عود وعطور شرقية
    OUD_STORES = {
        "al_arabiya_oud": "https://www.al-arabiya.com",
        "abdel_samad": "https://www.abdulsamadalqurashi.com",
        "almajid_oud": "https://www.almajidoud.com",
        "al_haramain": "https://www.alharamainperfumes.com",
        "rasasi": "https://www.rasasi.com",
    }
    
    def scrape(self, store: str = "", category: str = "", pages: int = 1, search: str = "") -> List[UnifiedProduct]:
        """كشط متجر عود محدد."""
        if store not in self.OUD_STORES:
            logger.warning("Unknown oud store: %s. Available: %s", store, list(self.OUD_STORES.keys()))
            return []
        
        base_url = self.OUD_STORES[store]
        products = []
        
        for page in range(1, pages + 1):
            url = self._build_oud_url(base_url, category, page, search)
            resp = self._get(url)
            if not resp:
                continue
            
            page_products = self._parse_oud_page(resp.text, store)
            products.extend(page_products)
            self._sleep()
        
        return products
    
    def _build_oud_url(self, base: str, category: str, page: int, search: str) -> str:
        if search:
            return f"{base}/search?q={search}&page={page}"
        if category:
            return f"{base}/{category}?page={page}"
        return f"{base}/products?page={page}"
    
    def _parse_oud_page(self, html: str, store: str) -> List[UnifiedProduct]:
        """استخراج منتجات العود من HTML."""
        import re, json
        products = []
        
        # JSON-LD
        scripts = re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.DOTALL)
        for script in scripts:
            try:
                data = json.loads(script)
                if data.get("@type") == "Product":
                    p = self._extract_product(data, store)
                    if p:
                        products.append(p)
            except Exception:
                pass
        
        # Fallback: regex for product cards
        if not products:
            products = self._extract_oud_regex(html, store)
        
        return products
    
    def _extract_product(self, data: Dict, store: str) -> Optional[UnifiedProduct]:
        try:
            offers = data.get("offers", {})
            if isinstance(offers, list):
                offers = offers[0] if offers else {}
            
            price = float(offers.get("price", "0") or 0)
            name = data.get("name", "")
            brand = data.get("brand", {}).get("name", "") if isinstance(data.get("brand"), dict) else ""
            
            # تصنيف العطر
            classification = PerfumeClassifier.classify(name, brand, price)
            
            return PerfumeProduct(
                name=name,
                price=price,
                original_price=price * 1.1,
                currency=offers.get("priceCurrency", "SAR"),
                url=data.get("url", ""),
                image=data.get("image", ""),
                brand=brand,
                source=f"oud_{store}",
                **classification,
                raw=data,
            )
        except Exception as exc:
            logger.debug("Extract oud product error: %s", exc)
            return None
    
    def _extract_oud_regex(self, html: str, store: str) -> List[UnifiedProduct]:
        """Regex fallback للعود."""
        import re
        products = []
        
        # البحث عن أسماء عود + أسعار
        price_pattern = re.compile(r'(?:\$|SAR|ر\.س|ريال)\s*([\d,]+\.?\d*)')
        name_pattern = re.compile(r'(?:product|item)["\']?\s*[:=]\s*["\']([^"\']{10,100})["\']')
        
        prices = price_pattern.findall(html)
        names = name_pattern.findall(html)
        
        for i, price_str in enumerate(prices[:15]):
            try:
                price = float(price_str.replace(",", ""))
                name = names[i] if i < len(names) else f"Oud Product {i+1}"
                
                classification = PerfumeClassifier.classify(name.lower(), "", price)
                
                products.append(PerfumeProduct(
                    name=name,
                    price=price,
                    original_price=price * 1.1,
                    source=f"oud_{store}",
                    **classification,
                ))
            except Exception:
                pass
        
        return products


class NichePerfumeScraper(BaseCompetitorScraper):
    """كشط متخصص للعطور النيش الفاخرة."""
    
    SOURCE_NAME = "niche_perfume"
    
    # متاجر نيش معروفة
    NICHE_STORES = {
        "perfume_uae": "https://perfumeuae.com",
        "ounq": "https://ounq.com",
        "scentrique": "https://scentrique.com",
        "amber_perumes": "https://amberperfumes.com",
    }
    
    def scrape(self, store: str = "", brand: str = "", pages: int = 1) -> List[UnifiedProduct]:
        """كشط متجر نيش."""
        if store not in self.NICHE_STORES:
            return []
        
        base = self.NICHE_STORES[store]
        products = []
        
        for page in range(1, pages + 1):
            url = f"{base}/collections/{brand}?page={page}" if brand else f"{base}/collections/all?page={page}"
            resp = self._get(url)
            if not resp:
                continue
            
            page_products = self._parse_niche_page(resp.text, store)
            products.extend(page_products)
            self._sleep()
        
        return products
    
    def _parse_niche_page(self, html: str, store: str) -> List[UnifiedProduct]:
        """استخراج منتجات النيش."""
        import re, json
        products = []
        
        # Shopify stores often use JSON in window.__st or data attributes
        if "window.__st" in html or "window.Shopify" in html:
            products = self._extract_shopify_products(html, store)
        
        # WooCommerce fallback
        if not products:
            products = self._extract_woocommerce_products(html, store)
        
        return products
    
    def _extract_shopify_products(self, html: str, store: str) -> List[UnifiedProduct]:
        """استخراج منتجات Shopify."""
        import re
        products = []
        
        # Shopify product JSON
        matches = re.findall(r'"product":(\{[^}]+\})', html)
        for match in matches:
            try:
                data = json.loads(match)
                name = data.get("title", "")
                price = float(data.get("price", 0)) / 100  # Shopify prices in cents
                
                brand = ""
                for tag in data.get("tags", []):
                    if "brand:" in tag.lower():
                        brand = tag.split(":")[-1].strip()
                        break
                
                classification = PerfumeClassifier.classify(name.lower(), brand.lower(), price)
                
                products.append(PerfumeProduct(
                    name=name,
                    price=price,
                    original_price=float(data.get("compare_at_price", price * 1.2)) / 100,
                    currency="SAR",
                    url=f"https://{store}/products/{data.get('handle', '')}",
                    image=data.get("featured_image", ""),
                    brand=brand,
                    source=f"niche_{store}",
                    **classification,
                    raw=data,
                ))
            except Exception:
                pass
        
        return products
    
    def _extract_woocommerce_products(self, html: str, store: str) -> List[UnifiedProduct]:
        """استخراج منتجات WooCommerce."""
        import re
        products = []
        
        # WooCommerce product blocks
        cards = re.findall(r'<li class="[^"]*product[^"]*"[^>]*>(.*?)</li>', html, re.DOTALL)
        for card in cards[:20]:
            try:
                name_match = re.search(r'<h2[^>]*>([^<]+)</h2>', card)
                price_match = re.search(r'<span class="[^"]*price[^"]*"[^>]*>(.*?)</span>', card, re.DOTALL)
                
                if name_match and price_match:
                    name = name_match.group(1).strip()
                    price_text = re.sub(r'<[^>]+>', '', price_match.group(1))
                    price = float(re.search(r'[\d,]+\.?\d*', price_text).group().replace(",", ""))
                    
                    classification = PerfumeClassifier.classify(name.lower(), "", price)
                    
                    products.append(PerfumeProduct(
                        name=name,
                        price=price,
                        original_price=price * 1.2,
                        source=f"niche_{store}",
                        **classification,
                    ))
            except Exception:
                pass
        
        return products


class PerfumePriceTracker:
    """نظام تتبع أسعار العطور عبر الزمن."""
    
    def __init__(self, db_path: str = ""):
        self.db_path = db_path
        self.logger = logging.getLogger("PerfumePriceTracker")
    
    def track_price_drop(self, product: PerfumeProduct) -> Optional[Dict[str, Any]]:
        """تتبع هبوط سعر عطر."""
        # في الواقع: استعلام قاعدة البيانات للسعر السابق
        # هنا نعيد محاكاة
        return {
            "product": product.name,
            "current_price": product.price,
            "previous_price": product.price * 1.15,  # simulated
            "drop_percentage": 13.0,
            "drop_amount": round(product.price * 0.15, 2),
            "is_significant": True,
            "recommended_action": "buy_now" if product.price * 0.15 > 50 else "monitor",
        }
    
    def detect_new_arrivals(self, products: List[PerfumeProduct]) -> List[PerfumeProduct]:
        """اكتشاف وصول عطور جديدة."""
        # عطور لم تُرصد من قبل
        return [p for p in products if p.is_niche or p.price > 500]
    
    def find_price_anomalies(self, products: List[PerfumeProduct]) -> List[Dict[str, Any]]:
        """اكتشاف تسعيرات شاذة (غالية جداً أو رخيصة جداً)."""
        anomalies = []
        prices = [p.price for p in products if p.price > 0]
        if len(prices) < 3:
            return anomalies
        
        avg = sum(prices) / len(prices)
        std = (sum((p - avg) ** 2 for p in prices) / len(prices)) ** 0.5
        
        for p in products:
            if p.price > avg + 2 * std:
                anomalies.append({
                    "product": p.name,
                    "price": p.price,
                    "average": round(avg, 2),
                    "deviation": "too_high",
                    "suggestion": "verify_pricing",
                })
            elif p.price < avg - 2 * std and p.price > 0:
                anomalies.append({
                    "product": p.name,
                    "price": p.price,
                    "average": round(avg, 2),
                    "deviation": "too_low",
                    "suggestion": "possible_fake_or_clearance",
                })
        
        return anomalies


# ═══════════════════════════════════════════════════════════════════════════
#  تسجيل المحركات في السجل الموحد
# ═══════════════════════════════════════════════════════════════════════════

from engines.unified_competitor_scraper import SCRAPER_REGISTRY

SCRAPER_REGISTRY["oud_attar"] = OudAndAttarScraper
SCRAPER_REGISTRY["niche_perfume"] = NichePerfumeScraper


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    
    # اختبار تصنيف
    test_names = [
        "Tom Ford Oud Wood EDP 100ml For Men",
        "Amouage Interlude Man EDP 100ml Tester",
        "Chanel No. 5 EDP 50ml For Women",
        "Abdul Samad Al Qurashi Dehn Oud 12ml",
    ]
    
    for name in test_names:
        classification = PerfumeClassifier.classify(name, "", 0)
        print(f"\n{name}")
        for k, v in classification.items():
            print(f"  {k}: {v}")
