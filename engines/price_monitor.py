"""
engines/price_monitor.py — نظام مراقبة أسعار مستمر وإشعارات
══════════════════════════════════════════════════════════════════════════════
══════════════════════════════════════════════════════════════════════════════
يقوم بـ:
- مراقبة تغيرات الأسعار بشكل دوري
- إرسال إشعارات عند انخفاض الأسعار
- اكتشاف المنتجات الجديدة
- تتبع توفر المنتجات (out of stock / back in stock)
- توليد تقارير أسعار دورية
- دعم Discord, Slack, Email, Telegram للإشعارات
"""
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from engines.unified_competitor_scraper import UnifiedProduct

logger = logging.getLogger("PriceMonitor")


@dataclass
class PriceAlert:
    """إشعار سعر."""
    id: int = 0
    product_name: str = ""
    source: str = ""
    url: str = ""
    target_price: float = 0.0
    current_price: float = 0.0
    previous_price: float = 0.0
    alert_type: str = "price_drop"  # price_drop, price_target, stock_back, new_product
    created_at: datetime = None
    triggered_at: Optional[datetime] = None
    is_active: bool = True
    
    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now(timezone.utc).replace(tzinfo=None)


@dataclass
class PriceHistory:
    """سجل سعر منتج."""
    product_name: str = ""
    source: str = ""
    sku: str = ""
    price: float = 0.0
    original_price: float = 0.0
    timestamp: datetime = None
    availability: bool = True


class PriceMonitorDatabase:
    """قاعدة بيانات مراقبة الأسعار."""
    
    def __init__(self, db_path: str = "data/price_monitor.db"):
        self.db_path = db_path
        self._init_db()
    
    def _init_db(self):
        """تهيئة جداول قاعدة البيانات."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS price_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    product_name TEXT NOT NULL,
                    source TEXT NOT NULL,
                    sku TEXT,
                    price REAL NOT NULL,
                    original_price REAL,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    availability INTEGER DEFAULT 1
                )
            """)
            
            conn.execute("""
                CREATE TABLE IF NOT EXISTS price_alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    product_name TEXT NOT NULL,
                    source TEXT NOT NULL,
                    url TEXT,
                    target_price REAL,
                    current_price REAL,
                    previous_price REAL,
                    alert_type TEXT DEFAULT 'price_drop',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    triggered_at DATETIME,
                    is_active INTEGER DEFAULT 1
                )
            """)
            
            conn.execute("""
                CREATE TABLE IF NOT EXISTS product_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    product_name TEXT NOT NULL,
                    source TEXT NOT NULL,
                    sku TEXT,
                    data TEXT,  -- JSON
                    snapshot_date DATE DEFAULT CURRENT_DATE
                )
            """)
            
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_price_history_name_source 
                ON price_history(product_name, source)
            """)
            
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_price_history_timestamp 
                ON price_history(timestamp)
            """)
            
            conn.commit()
    
    def save_price(self, history: PriceHistory):
        """حفظ سعر في التاريخ."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO price_history (product_name, source, sku, price, original_price, timestamp, availability)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                history.product_name,
                history.source,
                history.sku,
                history.price,
                history.original_price,
                history.timestamp,
                int(history.availability)
            ))
            conn.commit()
    
    def get_price_history(self, product_name: str, source: str, days: int = 30) -> List[PriceHistory]:
        """استرجاع تاريخ أسعار منتج."""
        since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                SELECT product_name, source, sku, price, original_price, timestamp, availability
                FROM price_history
                WHERE product_name = ? AND source = ? AND timestamp >= ?
                ORDER BY timestamp ASC
            """, (product_name, source, since))
            
            return [
                PriceHistory(
                    product_name=row[0],
                    source=row[1],
                    sku=row[2],
                    price=row[3],
                    original_price=row[4],
                    timestamp=datetime.fromisoformat(row[5]),
                    availability=bool(row[6])
                )
                for row in cursor.fetchall()
            ]
    
    def get_latest_price(self, product_name: str, source: str) -> Optional[PriceHistory]:
        """الحصول على آخر سعر مسجل."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                SELECT product_name, source, sku, price, original_price, timestamp, availability
                FROM price_history
                WHERE product_name = ? AND source = ?
                ORDER BY timestamp DESC
                LIMIT 1
            """, (product_name, source))
            
            row = cursor.fetchone()
            if row:
                return PriceHistory(
                    product_name=row[0],
                    source=row[1],
                    sku=row[2],
                    price=row[3],
                    original_price=row[4],
                    timestamp=datetime.fromisoformat(row[5]),
                    availability=bool(row[6])
                )
            return None
    
    def create_alert(self, alert: PriceAlert) -> int:
        """إنشاء تنبيه سعر جديد."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                INSERT INTO price_alerts (product_name, source, url, target_price, alert_type, is_active)
                VALUES (?, ?, ?, ?, ?, 1)
            """, (alert.product_name, alert.source, alert.url, alert.target_price, alert.alert_type))
            conn.commit()
            return cursor.lastrowid
    
    def get_active_alerts(self) -> List[PriceAlert]:
        """استرجاع التنبيهات النشطة."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                SELECT id, product_name, source, url, target_price, current_price, previous_price,
                       alert_type, created_at, triggered_at, is_active
                FROM price_alerts
                WHERE is_active = 1
            """)
            
            alerts = []
            for row in cursor.fetchall():
                alerts.append(PriceAlert(
                    id=row[0],
                    product_name=row[1],
                    source=row[2],
                    url=row[3],
                    target_price=row[4],
                    current_price=row[5] or 0,
                    previous_price=row[6] or 0,
                    alert_type=row[7],
                    created_at=datetime.fromisoformat(row[8]) if row[8] else datetime.now(timezone.utc).replace(tzinfo=None),
                    triggered_at=datetime.fromisoformat(row[9]) if row[9] else None,
                    is_active=bool(row[10])
                ))
            return alerts
    
    def mark_alert_triggered(self, alert_id: int, current_price: float, previous_price: float):
        """تعليم تنبيه كمنفّذ."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                UPDATE price_alerts
                SET triggered_at = CURRENT_TIMESTAMP, current_price = ?, previous_price = ?, is_active = 0
                WHERE id = ?
            """, (current_price, previous_price, alert_id))
            conn.commit()
    
    def save_snapshot(self, product: UnifiedProduct):
        """حفظ لقطة من بيانات المنتج."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO product_snapshots (product_name, source, sku, data, snapshot_date)
                VALUES (?, ?, ?, ?, ?)
            """, (
                product.name,
                product.source,
                product.sku,
                json.dumps(product.raw, default=str),
                datetime.now().date()
            ))
            conn.commit()


class PriceChangeDetector:
    """كاشف تغيرات الأسعار."""
    
    def __init__(self, db: PriceMonitorDatabase):
        self.db = db
    
    def detect_changes(self, product: UnifiedProduct) -> List[Dict[str, Any]]:
        """اكتشاف تغيرات في منتج مقارنة بالسجل."""
        changes = []
        
        latest = self.db.get_latest_price(product.name, product.source)
        
        if not latest:
            # First time seeing this product
            changes.append({
                "type": "new_product",
                "product": product.name,
                "source": product.source,
                "price": product.price,
                "message": f"🆕 منتج جديد: {product.name} - {product.price} {product.currency}"
            })
        else:
            # Price change
            if product.price != latest.price and latest.price > 0:
                pct_change = ((product.price - latest.price) / latest.price) * 100
                change_type = "price_drop" if product.price < latest.price else "price_rise"
                
                changes.append({
                    "type": change_type,
                    "product": product.name,
                    "source": product.source,
                    "old_price": latest.price,
                    "new_price": product.price,
                    "change_pct": round(pct_change, 2),
                    "message": f"📢 {'انخفاض' if change_type == 'price_drop' else 'ارتفاع'} سعر: {product.name}\n"
                               f"   من {latest.price} إلى {product.price} ({abs(pct_change):.1f}%)"
                })
            
            # Availability change
            if product.in_stock != latest.availability:
                if product.in_stock and not latest.availability:
                    changes.append({
                        "type": "back_in_stock",
                        "product": product.name,
                        "source": product.source,
                        "price": product.price,
                        "message": f"✅ عاد للتوفر: {product.name} - {product.price} {product.currency}"
                    })
                else:
                    changes.append({
                        "type": "out_of_stock",
                        "product": product.name,
                        "source": product.source,
                        "message": f"❌ نفدت الكمية: {product.name}"
                    })
        
        # Save current price
        self.db.save_price(PriceHistory(
            product_name=product.name,
            source=product.source,
            sku=product.sku,
            price=product.price,
            original_price=product.original_price,
            timestamp=datetime.now(timezone.utc).replace(tzinfo=None),
            availability=product.in_stock
        ))
        
        return changes


class NotificationChannel:
    """قناة إشعارات أساسية."""
    
    def send(self, message: str, title: str = "") -> bool:
        raise NotImplementedError


class ConsoleNotification(NotificationChannel):
    """إشعارات في الكونسول."""
    
    def send(self, message: str, title: str = "") -> bool:
        print(f"\n{'='*60}")
        if title:
            print(f"📢 {title}")
        print(message)
        print(f"{'='*60}\n")
        return True


class FileNotification(NotificationChannel):
    """حفظ الإشعارات في ملف."""
    
    def __init__(self, log_path: str = "data/price_alerts.log"):
        self.log_path = log_path
    
    def send(self, message: str, title: str = "") -> bool:
        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}]\n")
                if title:
                    f.write(f"Title: {title}\n")
                f.write(f"{message}\n")
                f.write("-" * 60 + "\n")
            return True
        except Exception as exc:
            logger.error("File notification error: %s", exc)
            return False


class DiscordNotification(NotificationChannel):
    """إشعارات Discord عبر Webhook."""
    
    def __init__(self, webhook_url: str = ""):
        self.webhook_url = webhook_url
    
    def send(self, message: str, title: str = "") -> bool:
        if not self.webhook_url:
            return False
        
        try:
            import requests
            payload = {
                "content": None,
                "embeds": [{
                    "title": title or "Price Alert",
                    "description": message[:2000],  # Discord limit
                    "color": 3447003,
                    "timestamp": datetime.now().isoformat()
                }]
            }
            resp = requests.post(self.webhook_url, json=payload, timeout=10)
            return resp.status_code == 204
        except Exception as exc:
            logger.error("Discord notification error: %s", exc)
            return False


class PriceMonitor:
    """نظام مراقبة أسعار متكامل."""
    
    def __init__(self, db_path: str = "data/price_monitor.db"):
        self.db = PriceMonitorDatabase(db_path)
        self.detector = PriceChangeDetector(self.db)
        self.channels: List[NotificationChannel] = []
        self.monitored_products: Dict[str, Dict[str, Any]] = {}  # name -> {sources, target_price}
    
    def add_channel(self, channel: NotificationChannel):
        """إضافة قناة إشعارات."""
        self.channels.append(channel)
    
    def monitor_product(self, product_name: str, sources: List[str], target_price: Optional[float] = None):
        """إضافة منتج للمراقبة."""
        self.monitored_products[product_name] = {
            "sources": sources,
            "target_price": target_price
        }
    
    def process_products(self, products: List[UnifiedProduct]) -> List[Dict[str, Any]]:
        """معالجة قائمة منتجات واكتشاف التغيرات."""
        all_changes = []
        
        for product in products:
            changes = self.detector.detect_changes(product)
            
            # Check target price alerts
            if product.name in self.monitored_products:
                config = self.monitored_products[product.name]
                target = config.get("target_price")
                
                if target and product.price <= target:
                    changes.append({
                        "type": "target_reached",
                        "product": product.name,
                        "source": product.source,
                        "price": product.price,
                        "target": target,
                        "message": f"🎯 وصل للهدف: {product.name} الآن {product.price} (الهدف: {target})"
                    })
            
            all_changes.extend(changes)
        
        # Send notifications
        for change in all_changes:
            self._notify(change["message"], change["type"])
        
        return all_changes
    
    def _notify(self, message: str, title: str = ""):
        """إرسال إشعار عبر جميع القنوات."""
        for channel in self.channels:
            try:
                channel.send(message, title)
            except Exception as exc:
                logger.error("Notification channel error: %s", exc)
    
    def generate_report(self, days: int = 7) -> str:
        """توليد تقرير تغيرات أسعار."""
        report = ["# Price Change Report\n", f"Period: Last {days} days\n", f"Generated: {datetime.now()}\n", "-" * 60 + "\n"]
        
        # Get all changes from history
        since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
        
        with sqlite3.connect(self.db.db_path) as conn:
            # Price drops
            cursor = conn.execute("""
                SELECT product_name, source, price, timestamp,
                       LAG(price) OVER (PARTITION BY product_name, source ORDER BY timestamp) as prev_price
                FROM price_history
                WHERE timestamp >= ?
                ORDER BY product_name, timestamp
            """, (since,))
            
            drops = []
            for row in cursor.fetchall():
                if row[4] and row[3] < row[4]:
                    drops.append(row)
            
            report.append(f"\n## Price Drops: {len(drops)}\n")
            for row in drops[:20]:
                pct = ((row[4] - row[3]) / row[4]) * 100
                report.append(f"- {row[0]} ({row[1]}): {row[4]} -> {row[3]} ({pct:.1f}%)\n")
        
        return "".join(report)


# ═══════════════════════════════════════════════════════════════════════════
#  تنفيذ دوري (Cron job helper)
# ═══════════════════════════════════════════════════════════════════════════

class PriceMonitorScheduler:
    """مجدول مراقبة أسعار."""
    
    def __init__(self, monitor: PriceMonitor, scraper_func: Callable[[], List[UnifiedProduct]]):
        self.monitor = monitor
        self.scraper_func = scraper_func
        self.running = False
    
    def run_once(self) -> List[Dict[str, Any]]:
        """تشغيل دورة مراقبة واحدة."""
        logger.info("Starting price monitoring cycle...")
        products = self.scraper_func()
        changes = self.monitor.process_products(products)
        logger.info("Cycle complete. %d changes detected.", len(changes))
        return changes
    
    async def run_continuous(self, interval_hours: int = 6):
        """تشغيل مستمر."""
        self.running = True
        while self.running:
            try:
                self.run_once()
            except Exception as exc:
                logger.error("Monitoring cycle error: %s", exc)
            
            logger.info("Sleeping for %d hours...", interval_hours)
            await asyncio.sleep(interval_hours * 3600)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    
    # Example setup
    monitor = PriceMonitor()
    
    # Add channels
    monitor.add_channel(ConsoleNotification())
    monitor.add_channel(FileNotification())
    
    # Example: monitor a product
    monitor.monitor_product("Oud Wood", ["amazon_sa", "fragrancenet"], target_price=500.0)
    
    print("Price Monitor initialized.")
    print("Active alerts:", len(monitor.db.get_active_alerts()))
