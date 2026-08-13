"""services/scraper_service.py — إدارة روابط المنافسين + الكشط.

يدير ملف `competitors_list_v30.json` (سرد/إضافة عبر رابط محلي/حذف) ويلتفّ على
`engines.mahally_scraper.MahallyScraper` للكشط والتحقّق (لا يُعاد كتابة المحرّك).

الجزء النقي (استخراج المعرّف + إدارة JSON) قابل للاختبار دون شبكة؛ النداءات
الشبكية (تحقّق/كشط) مفصولة في دوال تستورد المحرّك كسولاً.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from conf.constants import COMPETITORS_FILE, COMPETITOR_DB_PATH, PROJECT_ROOT
from core.exceptions import PricingError, RepositoryError
from utils.data_paths import get_data_dir

_STORE_ID_RE = re.compile(r"/stores/(\d+)")
_SCRAPE_LOCK_TTL = 3 * 3600  # ثوانٍ — قفل أقدم من ذلك يُعدّ يتيماً فيُكسَر

logger = logging.getLogger("scraper_service")


# ═══════════════════════════════════════════════════════════════════════════
#  توسيع Competitor ليدعم مصادر متعددة
# ═══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class Competitor:
    """متجر منافس مُعرَّف محلياً — يدعم مصادر متعددة."""

    name: str
    store_url: str
    mahally_store_id: int = 0
    sitemap_url: str = ""
    source: str = "mahally"  # mahally, noon, amazon_sa, jarir, extra, salla, global
    source_config: Dict[str, Any] = field(default_factory=dict)
    is_active: bool = True
    last_scraped: str = ""
    total_products: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "store_url": self.store_url,
            "sitemap_url": self.sitemap_url,
            "mahally_store_id": self.mahally_store_id,
            "source": self.source,
            "source_config": self.source_config,
            "is_active": self.is_active,
            "last_scraped": self.last_scraped,
            "total_products": self.total_products,
        }

    @property
    def is_mahally(self) -> bool:
        return self.source == "mahally" and self.mahally_store_id > 0

    @property
    def display_name(self) -> str:
        source_labels = {
            "mahally": "🛒",
            "noon": "🌙",
            "amazon_sa": "📦",
            "jarir": "📚",
            "extra": "⚡",
            "salla": "🛍️",
            "global": "🌍",
        }
        icon = source_labels.get(self.source, "🏪")
        return f"{icon} {self.name}"


def extract_store_id(url: str) -> Optional[int]:
    """يستخرج معرّف متجر محلي من رابط `mahally.com/stores/ID`. خالص."""
    match = _STORE_ID_RE.search(url or "")
    return int(match.group(1)) if match else None


# ═══════════════════════════════════════════════════════════════════════════
#  ScraperService المُحدّث
# ═══════════════════════════════════════════════════════════════════════════

class ScraperService:
    """خدمة كشط المنافسين وإدارة روابطهم — دعم متعدد المصادر."""

    SUPPORTED_SOURCES = ["mahally", "noon", "amazon_sa", "jarir", "extra", "salla", "global"]

    def __init__(
        self,
        links_file: Path = COMPETITORS_FILE,
        competitor_db: Path = COMPETITOR_DB_PATH,
        price_monitor_db: str = "data/price_monitor.db",
    ) -> None:
        self._file = Path(links_file)
        self._db = str(competitor_db)
        self._price_monitor_db = price_monitor_db
        self._price_monitor = None
        # حالة تشخيصية عابرة: تبقي الصفحة عاملة، لكنها تمنع عرض فشل SQLite كفراغ صحيح.
        self._query_error: Optional[str] = None

    @property
    def query_error(self) -> Optional[str]:
        """رسالة آمنة للواجهة عن آخر فشل قراءة، أو ``None`` عند نجاح القراءة."""
        return self._query_error

    # ═══════════════════════════════════════════════════════════════════
    #  إدارة المنافسين (JSON)
    # ═══════════════════════════════════════════════════════════════════

    def _load_raw(self) -> list[dict[str, Any]]:
        if not self._file.exists():
            return []
        try:
            with open(self._file, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            return data if isinstance(data, list) else []
        except Exception as exc:
            raise RepositoryError("تعذّرت قراءة ملف المنافسين", error=str(exc)) from exc

    def _save_raw(self, data: list[dict[str, Any]]) -> None:
        self._file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._file.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
        tmp.replace(self._file)

    def list_competitors(self, source: Optional[str] = None) -> list[Competitor]:
        """يسرد المتاجر المُعرَّفة. يمكن تصفية حسب المصدر."""
        out: list[Competitor] = []
        for entry in self._load_raw():
            sid = entry.get("mahally_store_id", 0)
            src = entry.get("source", "mahally")
            if source and src != source:
                continue
            if src == "mahally" and not sid:
                continue
            out.append(Competitor(
                name=entry.get("name", f"store_{sid}"),
                store_url=entry.get("store_url", ""),
                mahally_store_id=int(sid) if sid else 0,
                sitemap_url=entry.get("sitemap_url", ""),
                source=src,
                source_config=entry.get("source_config", {}),
                is_active=entry.get("is_active", True),
                last_scraped=entry.get("last_scraped", ""),
                total_products=entry.get("total_products", 0),
            ))
        return out

    def list_sources(self) -> list[str]:
        """قائمة المصادر المُعرَّفة في ملف المنافسين."""
        sources = set()
        for entry in self._load_raw():
            sources.add(entry.get("source", "mahally"))
        return sorted(sources)

    def list_unlinked(self) -> list[dict[str, Any]]:
        """منافسون مُعرَّفون **بلا** مصدر كشط."""
        return [
            {"name": e.get("name", ""), "store_url": e.get("store_url", ""), "source": e.get("source", "mahally")}
            for e in self._load_raw()
            if not e.get("mahally_store_id") and e.get("source", "mahally") == "mahally"
        ]

    def add_competitor(self, name: str, url: str, source: str = "mahally",
                       source_config: Optional[Dict[str, Any]] = None) -> Competitor:
        """يضيف متجراً (يدعم مصادر متعددة)."""
        if not (name or "").strip():
            raise PricingError("اسم المتجر مطلوب")
        data = self._load_raw()
        if any(e.get("name") == name.strip() for e in data):
            raise PricingError(f"المتجر موجود مسبقاً: {name}")
        store_id = 0
        if source == "mahally":
            store_id = extract_store_id(url) or 0
            if not store_id:
                raise PricingError("رابط غير صالح لـ Mahally — استخدم mahally.com/stores/ID")
            if any(e.get("mahally_store_id") == store_id for e in data):
                raise PricingError(f"المتجر موجود مسبقاً (#{store_id})")
        competitor = Competitor(
            name=name.strip(), store_url=url.strip(), mahally_store_id=store_id,
            source=source, source_config=source_config or {},
        )
        data.append(competitor.to_dict())
        self._save_raw(data)
        return competitor

    def add_noon_competitor(self, name: str, search_term: str) -> Competitor:
        return self.add_competitor(name, "", "noon", {"search_term": search_term, "category": "beauty-and-health"})

    def add_amazon_competitor(self, name: str, search_term: str, marketplace: str = "sa") -> Competitor:
        return self.add_competitor(name, "", "amazon_sa", {"search_term": search_term, "marketplace": marketplace})

    def add_salla_competitor(self, name: str, store_domain: str, category: str = "") -> Competitor:
        return self.add_competitor(name, store_domain, "salla", {"store_domain": store_domain, "category": category})

    def remove_competitor(self, name: str) -> bool:
        data = self._load_raw()
        kept = [e for e in data if e.get("name") != name and e.get("mahally_store_id") != name]
        if len(kept) == len(data):
            return False
        self._save_raw(kept)
        return True

    def update_competitor_stats(self, name: str, total_products: int, last_scraped: str = ""):
        data = self._load_raw()
        for entry in data:
            if entry.get("name") == name:
                entry["total_products"] = total_products
                entry["last_scraped"] = last_scraped or datetime.now().isoformat()
                break
        self._save_raw(data)

    # ═══════════════════════════════════════════════════════════════════
    #  الكشط — دعم متعدد المصادر
    # ═══════════════════════════════════════════════════════════════════

    def _mahally_scraper(self) -> Any:
        if str(PROJECT_ROOT) not in sys.path:
            sys.path.insert(0, str(PROJECT_ROOT))
        from engines.mahally_scraper import MahallyScraper
        return MahallyScraper(db_path=self._db)

    def _unified_scraper(self) -> Any:
        if str(PROJECT_ROOT) not in sys.path:
            sys.path.insert(0, str(PROJECT_ROOT))
        from engines.unified_competitor_scraper import UnifiedScraper
        return UnifiedScraper()

    def _get_price_monitor(self):
        if self._price_monitor is None:
            if str(PROJECT_ROOT) not in sys.path:
                sys.path.insert(0, str(PROJECT_ROOT))
            from engines.price_monitor import PriceMonitor, ConsoleNotification, FileNotification
            self._price_monitor = PriceMonitor(db_path=self._price_monitor_db)
            self._price_monitor.add_channel(ConsoleNotification())
            self._price_monitor.add_channel(FileNotification())
        return self._price_monitor

    def validate_store(self, store_id: int) -> dict[str, Any]:
        return self._mahally_scraper().get_store_info(store_id)

    def scrape_and_save(self, competitor: Competitor, rebuild: bool = True) -> int:
        """يكشط متجراً حسب مصدره ويحفظه.

        ``rebuild=False`` تمرّره الجولة (``scrape_all``) حصراً: إعادة بناء كاش
        الفرص تكلف ~80 ثانية (قياس 2026-07-09) — لكل-منافس داخل جولة تحوّل
        3.5 دقيقة كشط إلى ~45 دقيقة؛ الجولة تعيد البناء مرة واحدة في نهايتها.
        الكشط المفرد (زر متجر واحد) يبقى ``True`` — سلوكه لم يتغيّر.
        """
        if competitor.source == "mahally" and competitor.mahally_store_id:
            count = self._scrape_mahally(competitor)
        elif competitor.source in ("noon", "amazon_sa", "jarir", "extra"):
            count = self._scrape_unified(competitor)
        elif competitor.source == "salla":
            count = self._scrape_salla(competitor)
        elif competitor.source == "global":
            count = self._scrape_global(competitor)
        else:
            logger.warning("Unsupported source for %s: %s", competitor.name, competitor.source)
            return 0
        # خطاف إضافي بحت: إعادة بناء كاش الفرص بعد كشط ناجح — لا يمسّ القفل، وفشله لا يُفشل الكشط.
        if rebuild and count > 0:
            try:
                import time as _t
                from services.opportunity_service import rebuild_opportunity_scores
                _t0 = _t.perf_counter()
                _n = rebuild_opportunity_scores()
                logger.info("🎯 كاش الفرص: %d صفّ في %.0fs", _n, _t.perf_counter() - _t0)
            except Exception as exc:
                logger.warning("إعادة بناء كاش الفرص فشلت (تجاهل): %s", exc)
        return count

    def _scrape_mahally(self, competitor: Competitor) -> int:
        scraper = self._mahally_scraper()
        products = scraper.scrape_store(competitor.mahally_store_id, competitor.name)
        if not products:
            return 0
        count = scraper.save_to_db(products, competitor.name)
        self.update_competitor_stats(competitor.name, count)
        return count

    def _scrape_unified(self, competitor: Competitor) -> int:
        scraper = self._unified_scraper()
        config = competitor.source_config or {}
        products = scraper.scrape(
            source=competitor.source,
            category=config.get("category", ""),
            pages=config.get("pages", 1),
            search=config.get("search_term", config.get("query", "")),
        )
        if not products:
            return 0
        count = self._save_unified_products(products, competitor.name)
        self.update_competitor_stats(competitor.name, count)
        try:
            self._get_price_monitor().process_products(products)
        except Exception as exc:
            logger.warning("Price monitoring failed: %s", exc)
        return count

    def _scrape_salla(self, competitor: Competitor) -> int:
        if str(PROJECT_ROOT) not in sys.path:
            sys.path.insert(0, str(PROJECT_ROOT))
        from engines.salla_scraper import SallaStoreScraper
        config = competitor.source_config or {}
        scraper = SallaStoreScraper(store_domain=config.get("store_domain", competitor.store_url), delay=1.5)
        products = scraper.scrape(category=config.get("category", ""), pages=config.get("pages", 1))
        if not products:
            return 0
        count = self._save_unified_products(products, competitor.name)
        self.update_competitor_stats(competitor.name, count)
        return count

    def _scrape_global(self, competitor: Competitor) -> int:
        if str(PROJECT_ROOT) not in sys.path:
            sys.path.insert(0, str(PROJECT_ROOT))
        from engines.global_scraper import AmazonScraper, FragranceNetScraper
        config = competitor.source_config or {}
        source_type = config.get("global_source", "amazon")
        if source_type == "amazon":
            scraper = AmazonScraper(marketplace=config.get("marketplace", "sa"))
        elif source_type == "fragrancenet":
            scraper = FragranceNetScraper()
        else:
            return 0
        products = scraper.scrape(search=config.get("search_term", ""), pages=config.get("pages", 1))
        if not products:
            return 0
        count = self._save_unified_products(products, competitor.name)
        self.update_competitor_stats(competitor.name, count)
        return count

    def _save_unified_products(self, products: List[Any], competitor_name: str) -> int:
        import sqlite3
        saved = 0
        with sqlite3.connect(self._db) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS competitor_products_store (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    competitor TEXT NOT NULL,
                    product_name TEXT NOT NULL,
                    price REAL NOT NULL,
                    original_price REAL,
                    currency TEXT DEFAULT 'SAR',
                    image_url TEXT,
                    product_url TEXT,
                    brand TEXT,
                    category TEXT,
                    sku TEXT,
                    description TEXT,
                    availability INTEGER DEFAULT 1,
                    rating_count INTEGER DEFAULT 0,
                    rating_avg REAL,
                    first_seen_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    raw_data TEXT
                )
            """)
            for product in products:
                try:
                    conn.execute("""
                        INSERT OR REPLACE INTO competitor_products_store
                        (competitor, product_name, price, original_price, currency,
                         image_url, product_url, brand, category, sku, description,
                         availability, rating_count, rating_avg, updated_at, raw_data)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?)
                    """, (
                        competitor_name,
                        getattr(product, 'name', '')[:300],
                        getattr(product, 'price', 0),
                        getattr(product, 'original_price', 0),
                        getattr(product, 'currency', 'SAR'),
                        getattr(product, 'image', '')[:500],
                        getattr(product, 'url', '')[:500],
                        getattr(product, 'brand', '')[:100],
                        getattr(product, 'category', '')[:100],
                        getattr(product, 'sku', '')[:100],
                        getattr(product, 'description', '')[:500],
                        int(getattr(product, 'in_stock', True)),
                        getattr(product, 'rating_count', 0),
                        getattr(product, 'rating_avg', None),
                        json.dumps(getattr(product, 'raw', {}), default=str)[:2000],
                    ))
                    saved += 1
                except Exception as exc:
                    logger.debug("Save product error: %s", exc)
            conn.commit()
        return saved

    # ═══════════════════════════════════════════════════════════════════
    #  إحصائيات واستعلامات
    # ═══════════════════════════════════════════════════════════════════

    def scraped_stats(self, source: Optional[str] = None) -> dict[str, dict[str, Any]]:
        import sqlite3
        if not Path(self._db).exists():
            return {}
        try:
            con = sqlite3.connect(self._db)
            try:
                sql = "SELECT competitor, COUNT(*), MAX(updated_at) FROM competitor_products_store"
                if source:
                    sql += " WHERE competitor LIKE ?"
                    rows = con.execute(sql + " GROUP BY competitor", (f"{source}_%",)).fetchall()
                else:
                    rows = con.execute(sql + " GROUP BY competitor").fetchall()
            finally:
                con.close()
        except Exception:
            return {}
        return {str(name): {"count": int(cnt), "last": str(last or "")} for name, cnt, last in rows}

    def source_stats(self) -> dict[str, dict[str, Any]]:
        import sqlite3
        if not Path(self._db).exists():
            return {}
        stats = {}
        for src in self.SUPPORTED_SOURCES:
            stats[src] = {"competitors": 0, "products": 0, "last_scraped": ""}
        for src in self.list_sources():
            stats[src]["competitors"] = len(self.list_competitors(source=src))
        try:
            con = sqlite3.connect(self._db)
            try:
                rows = con.execute("SELECT competitor, COUNT(*) FROM competitor_products_store GROUP BY competitor").fetchall()
                for name, cnt in rows:
                    for src in self.SUPPORTED_SOURCES:
                        if str(name).startswith(f"{src}_") or str(name) in [c.name for c in self.list_competitors(source=src)]:
                            stats[src]["products"] += cnt
            finally:
                con.close()
        except Exception:
            pass
        return stats

    # ═══════════════════════════════════════════════════════════════════
    #  كشط جميع المتاجر
    # ═══════════════════════════════════════════════════════════════════

    def scrape_all(self, progress_cb: Optional[Any] = None, sources: Optional[List[str]] = None) -> dict[str, int]:
        lock = os.path.join(get_data_dir(), ".scrape.lock")
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            age = time.time() - os.path.getmtime(lock)
            if age <= _SCRAPE_LOCK_TTL:
                raise PricingError(f"⏳ كشط آخر قيد التشغيل منذ {int(age // 60)} دقيقة — انتظر انتهاءه.")
            logger.warning("كسر قفل كشط يتيم (عمر %.0f دقيقة): %s", age / 60, lock)
            os.remove(lock)
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, f"{os.getpid()},{time.time():.0f}".encode())
        os.close(fd)
        try:
            comps = self.list_competitors()
            if sources:
                comps = [c for c in comps if c.source in sources and c.is_active]
            else:
                comps = [c for c in comps if c.is_active]
            # ── جدولة تتنفّس: الأسخن حركةً أولاً (fail-open بالترتيب الأصلي) ──
            try:
                from services.volatility_service import heat_order, order_by_heat
                comps = order_by_heat(comps, heat_order())
            except Exception as exc:
                logger.warning("ترتيب الحرارة تعذّر (يُكمل بالترتيب الأصلي): %s", exc)
            results: dict[str, int] = {}
            for i, comp in enumerate(comps):
                if progress_cb is not None:
                    progress_cb(i, len(comps), comp.name)
                try:
                    results[comp.name] = self.scrape_and_save(comp, rebuild=False)
                except Exception as exc:
                    logger.error("Scrape failed for %s: %s", comp.name, exc)
                    results[comp.name] = -1
            # ── كاش الفرص مرة واحدة لنهاية الجولة (بدل ~80s × كل منافس) ──
            if any(v > 0 for v in results.values()):
                try:
                    from services.opportunity_service import rebuild_opportunity_scores
                    _t0 = time.time()
                    _n = rebuild_opportunity_scores()
                    logger.info(
                        "🎯 كاش الفرص (نهاية الجولة): %d صفّ في %.0fs",
                        _n, time.time() - _t0,
                    )
                except Exception as exc:
                    logger.warning("إعادة بناء كاش الفرص فشلت (تجاهل): %s", exc)
                # ── كاش «جديد عند المنافسين» (هـ1-تابع) — نهاية الجولة، تجاهل الخطأ ──
                try:
                    _t0 = time.time()
                    _nnp = self.rebuild_new_products_cache()
                    logger.info(
                        "🆕 كاش المنتجات الجديدة (نهاية الجولة): %d صفّ في %.0fs",
                        _nnp, time.time() - _t0,
                    )
                except Exception as exc:
                    logger.warning("إعادة بناء كاش المنتجات الجديدة فشلت (تجاهل): %s", exc)
                # ── كاش «أكبر منافس» (وسم الأقوى تقييماً) — نهاية الجولة، تجاهل الخطأ ──
                try:
                    from services.top_competitor_service import rebuild_top_competitor
                    _t0 = time.time()
                    _nt = rebuild_top_competitor()
                    logger.info(
                        "⭐ كاش أكبر منافس (نهاية الجولة): %d صفّ في %.0fs",
                        _nt, time.time() - _t0,
                    )
                except Exception as exc:
                    logger.warning("إعادة بناء كاش أكبر منافس فشلت (تجاهل): %s", exc)
                # ── تقييم المتاجر (🏪) — استعلام خفيف لكل متجر، نهاية الجولة، تجاهل الخطأ ──
                try:
                    from services.store_profile_service import refresh_store_profiles
                    _t0 = time.time()
                    _ns = refresh_store_profiles(self.list_competitors())
                    logger.info(
                        "🏪 تقييم المتاجر (نهاية الجولة): %d متجراً في %.0fs",
                        _ns, time.time() - _t0,
                    )
                except Exception as exc:
                    logger.warning("تحديث تقييم المتاجر فشل (تجاهل): %s", exc)
                # ── كاش أولوية «سعر أعلى» (يقين الطلب+ثقة المصدر) — بعد تقييم المتاجر
                #    مباشرة كي يقرأ trust_score طازجاً، نهاية الجولة، تجاهل الخطأ ──
                try:
                    from services.priority_engine import rebuild_product_priority_scores
                    _t0 = time.time()
                    _np = rebuild_product_priority_scores()
                    logger.info(
                        "⚡ كاش أولوية سعر أعلى (نهاية الجولة): %d صفّ في %.0fs",
                        _np, time.time() - _t0,
                    )
                except Exception as exc:
                    logger.warning("إعادة بناء كاش الأولوية فشلت (تجاهل): %s", exc)
                # ── اكتشاف متاجر عطور جديدة (🔎) — نهاية الجولة، تجاهل الخطأ ──
                try:
                    from services.store_discovery_service import refresh_discovered_stores
                    _t0 = time.time()
                    _known = [int(getattr(c, "mahally_store_id", 0) or 0)
                              for c in self.list_competitors()]
                    _nd = refresh_discovered_stores(_known)
                    logger.info(
                        "🔎 اكتشاف متاجر جديدة (نهاية الجولة): %d في %.0fs",
                        _nd, time.time() - _t0,
                    )
                except Exception as exc:
                    logger.warning("اكتشاف المتاجر الجديدة فشل (تجاهل): %s", exc)
            # ── تحديث حرارة المنافسين لجولة الكشط القادمة (لا يمسّ النتائج) ──
            try:
                from services.volatility_service import rebuild_competitor_heat
                _nh = rebuild_competitor_heat()
                logger.info("🌡️ حرارة المنافسين: %d متجراً", _nh)
            except Exception as exc:
                logger.warning("إعادة بناء الحرارة فشلت (تجاهل): %s", exc)
            return results
        finally:
            try:
                os.remove(lock)
            except OSError:
                pass

    # ═══════════════════════════════════════════════════════════════════
    #  دعم Price Monitor (إشعارات)
    # ═══════════════════════════════════════════════════════════════════

    def set_price_alert(self, product_name: str, target_price: float, sources: List[str] = None) -> bool:
        try:
            monitor = self._get_price_monitor()
            monitor.monitor_product(product_name, sources or [], target_price)
            return True
        except Exception as exc:
            logger.error("Set price alert failed: %s", exc)
            return False

    def get_price_changes(self, days: int = 7) -> str:
        try:
            monitor = self._get_price_monitor()
            return monitor.generate_report(days=days)
        except Exception as exc:
            logger.error("Generate report failed: %s", exc)
            return ""

    # ═══════════════════════════════════════════════════════════════════
    #  الأساليب القديمة (التوافق العكسي)
    # ═══════════════════════════════════════════════════════════════════

    def scrape_and_save_mahally(self, store_id: int, name: str) -> int:
        comp = Competitor(name=name, store_url="", mahally_store_id=store_id, source="mahally")
        return self._scrape_mahally(comp)

    # ═══════════════════════════════════════════════════════════════════
    #  بقية الدوال (new_products, out_of_stock, إلخ)
    # ═══════════════════════════════════════════════════════════════════

    def _ensure_schema(self) -> None:
        if getattr(self, "_schema_ok", False) or not Path(self._db).exists():
            return
        import sqlite3
        try:
            con = sqlite3.connect(self._db)
            try:
                cols = [r[1] for r in con.execute("PRAGMA table_info(competitor_products_store)")]
                if cols and "availability" not in cols:
                    con.execute("ALTER TABLE competitor_products_store ADD COLUMN availability INTEGER DEFAULT 1")
                con.execute("""
                    CREATE TABLE IF NOT EXISTS scrape_runs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT, competitor TEXT NOT NULL,
                        run_at TEXT NOT NULL, total_seen INTEGER DEFAULT 0,
                        new_count INTEGER DEFAULT 0, oos_count INTEGER DEFAULT 0)
                """)
                con.commit()
            finally:
                con.close()
            self._schema_ok = True
        except Exception as exc:
            logger.warning("تعذّر تهيئة مخطط قاعدة الكشط: %s", exc)

    def _query(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        import sqlite3
        # قاعدة غائبة تعني «لا بيانات بعد»؛ أما فشل القراءة فيجب أن يبقى مرئياً للواجهة.
        self._query_error = None
        self._ensure_schema()
        if not Path(self._db).exists():
            return []
        try:
            # قراءة تحليلية فقط (كل مستدعيات _query هي SELECT) ⇒ mode=ro:
            # لا تأخذ قفل كتابة على القاعدة الحيّة أثناء عرض الصفحة.
            con = sqlite3.connect(
                "file:" + str(self._db).replace("\\", "/") + "?mode=ro", uri=True)
            con.row_factory = sqlite3.Row
            try:
                rows = con.execute(sql, params).fetchall()
            finally:
                con.close()
        except Exception:
            # لا نرفع الاستثناء كي لا تنكسر الصفحة، لكن لا نساوي بين العطب والنتيجة الفارغة.
            self._query_error = "تعذّر قراءة بيانات الكشط. أعد المحاولة لاحقاً."
            logger.exception("تعذّر استعلام بيانات الكشط")
            return []
        return [dict(r) for r in rows]

    def competitor_names(self) -> list[str]:
        rows = self._query("SELECT DISTINCT competitor FROM competitor_products_store ORDER BY competitor")
        return [str(r["competitor"]) for r in rows if r.get("competitor")]

    def new_products(self, competitor: Optional[str] = None, since_days: Optional[int] = None, limit: int = 500) -> list[dict[str, Any]]:
        where = ["cps.price > 0", "cps.availability != 0"]
        params: list[Any] = []
        join = ""
        if competitor:
            where.append("cps.competitor = ?")
            params.append(competitor)
        if since_days is not None:
            where.append("cps.first_seen_at >= datetime('now','localtime',?)")
            params.append(f"-{int(since_days)} days")
        else:
            has_runs = self._query("SELECT 1 FROM scrape_runs LIMIT 1")
            if has_runs:
                # JOIN على تجميع يُحسب مرة واحدة (~484 صف) بدل استعلام مرتبط يُقيَّم
                # لكل صف من ~176k (قياس 2026-07-09: 4.9s ⇒ 0.25s بنتائج متطابقة).
                # منافس بلا سجل كشط يُستبعد في الحالتين (NULL/لا-تطابق) — السلوك محفوظ.
                join = (" JOIN (SELECT competitor, MAX(run_at) AS last_run"
                        " FROM scrape_runs GROUP BY competitor) lr"
                        " ON lr.competitor = cps.competitor")
                where.append("cps.first_seen_at >= lr.last_run")
            else:
                where.append("cps.first_seen_at >= datetime('now','localtime','-7 days')")
        sql = (
            "SELECT cps.competitor, cps.product_name, cps.price, cps.image_url,"
            " cps.product_url, cps.brand, cps.first_seen_at"
            f" FROM competitor_products_store cps{join}"
            f" WHERE {' AND '.join(where)}"
            " ORDER BY cps.first_seen_at DESC LIMIT ?"
        )
        params.append(int(limit))
        return self._query(sql, tuple(params))

    def new_products_cached(self, competitor: Optional[str] = None, limit: int = 10000) -> list[dict[str, Any]]:
        """يقرأ ``new_products_cache`` (هـ1-تابع: ``rebuild_new_products_cache`` وقت
        الكشط) بدل تشغيل JOIN حيّ ثقيل (~9-12s على 430k صف، قياس 2026-07-22) عند كل
        فتح صفحة. غياب الكاش (قبل أول جولة كشط بعد هذا الإصدار) ⇒ رجوع آمن للاستعلام
        الحيّ (بطيء لكن صحيح) — لا صفحة فارغة خطأً."""
        import sqlite3
        if not Path(self._db).exists():
            return []
        try:
            con = sqlite3.connect(
                "file:" + str(self._db).replace("\\", "/") + "?mode=ro", uri=True)
            try:
                has_cache = con.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='new_products_cache'"
                ).fetchone()
            finally:
                con.close()
        except Exception:
            has_cache = None
        if not has_cache:
            return self.new_products(competitor=competitor, limit=limit)
        where = ["1=1"]
        params: list[Any] = []
        if competitor:
            where.append("competitor = ?")
            params.append(competitor)
        sql = (f"SELECT competitor, product_name, price, image_url, product_url, brand,"
               f" first_seen_at FROM new_products_cache WHERE {' AND '.join(where)}"
               " ORDER BY first_seen_at DESC LIMIT ?")
        params.append(int(limit))
        return self._query(sql, tuple(params))

    def rebuild_new_products_cache(self) -> int:
        """يعيد بناء ``new_products_cache`` (نتيجة «منذ آخر كشطة» الثقيلة) وقت
        الكشط — نفس نمط ``rebuild_opportunity_scores`` (DROP+CREATE+INSERT ذرّي
        داخل BEGIN صريح؛ فشل ⇒ تراجع كامل فيبقى الكاش القديم سليماً)."""
        import sqlite3
        if not Path(self._db).exists():
            return 0
        try:
            rows = self.new_products(limit=10000)  # الاستعلام الحيّ الثقيل — مرة واحدة هنا فقط
            con = sqlite3.connect(self._db)
            try:
                with con:
                    con.execute("BEGIN")
                    con.execute("DROP TABLE IF EXISTS new_products_cache")
                    con.execute(
                        "CREATE TABLE new_products_cache (competitor TEXT, product_name TEXT,"
                        " price REAL, image_url TEXT, product_url TEXT, brand TEXT,"
                        " first_seen_at TEXT)"
                    )
                    con.execute(
                        "CREATE INDEX idx_npc_competitor ON new_products_cache(competitor)")
                    con.executemany(
                        "INSERT INTO new_products_cache VALUES (?,?,?,?,?,?,?)",
                        [(r["competitor"], r["product_name"], r["price"], r["image_url"],
                          r["product_url"], r["brand"], r["first_seen_at"]) for r in rows],
                    )
                return len(rows)
            finally:
                con.close()
        except Exception as exc:
            logger.warning("إعادة بناء كاش المنتجات الجديدة فشلت (تجاهل): %s", exc)
            return 0

    def out_of_stock(self, competitor: Optional[str] = None, limit: int = 500) -> list[dict[str, Any]]:
        where = ["availability = 0"]
        params: list[Any] = []
        if competitor:
            where.append("competitor = ?")
            params.append(competitor)
        sql = f"SELECT competitor, product_name, price, image_url, product_url, brand, first_seen_at, updated_at FROM competitor_products_store WHERE {' AND '.join(where)} ORDER BY updated_at DESC LIMIT ?"
        params.append(int(limit))
        return self._query(sql, tuple(params))

    def out_of_stock_count(self, competitor: Optional[str] = None) -> int:
        where = ["availability = 0"]
        params: list[Any] = []
        if competitor:
            where.append("competitor = ?")
            params.append(competitor)
        rows = self._query(f"SELECT COUNT(*) AS n FROM competitor_products_store WHERE {' AND '.join(where)}", tuple(params))
        return int(rows[0]["n"]) if rows else 0

    def last_scrape_run(self, competitor: str) -> dict[str, Any]:
        rows = self._query("SELECT run_at, total_seen, new_count, oos_count FROM scrape_runs WHERE competitor=? ORDER BY id DESC LIMIT 1", (competitor,))
        return rows[0] if rows else {}
