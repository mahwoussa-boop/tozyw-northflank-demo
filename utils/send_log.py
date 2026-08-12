"""utils/send_log.py — سجل إرسال Make + حارس تكرار الـSKU (pricing_v18.db).

يدوّن كل محاولة إرسال (نجاح/فشل/تخطٍّ) في جدول send_log، ويكشف تكرار المنتج الجديد
عبر الـSKU مقابل السجل (success=1 و webhook_type='new') ثم our_catalog. آمن تماماً:
كل دالة داخل try/except، وفشل التدوين لا يوقف الإرسال أبداً (تحذير فقط).
"""
import logging
import re
import sqlite3
from datetime import datetime

from utils.data_paths import get_data_db_path

logger = logging.getLogger("SendLog")


def _db(db_path=None):
    return sqlite3.connect(db_path or get_data_db_path("pricing_v18.db"), timeout=30)


def init_send_log(db_path=None) -> None:
    """ينشئ جدول send_log وفهرس الـSKU إن غابا (idempotent). آمن: أي خطأ يُبتلع بتحذير."""
    try:
        conn = _db(db_path)
        conn.execute("""CREATE TABLE IF NOT EXISTS send_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT, batch_id TEXT, webhook_type TEXT,
            sku TEXT, product_name TEXT, category TEXT, brand TEXT,
            http_status INTEGER, success INTEGER DEFAULT 0, response_excerpt TEXT,
            price REAL, comp_price REAL
        )""")
        # هجرة idempotent للقاعدة القائمة (نمط أعمدة mahally_scraper): إضافة فقط.
        for _col in ("price REAL", "comp_price REAL"):
            try:
                conn.execute(f"ALTER TABLE send_log ADD COLUMN {_col}")
            except sqlite3.OperationalError:
                pass  # العمود موجود مسبقاً
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sendlog_sku ON send_log(sku)")
        conn.commit(); conn.close()
    except Exception as e:
        logger.warning("تعذّر إنشاء جدول send_log: %s", e)


def _fnum(v):
    """float أو None — NULL في القاعدة يعني «غير معروف»، لا صفراً (لا سعر مجاني وهمي)."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def log_send(webhook_type, *, sku="", product_name="", category="", brand="",
             http_status=0, success=False, response_excerpt="", batch_id="",
             price=None, comp_price=None, db_path=None) -> None:
    """يدوّن محاولة إرسال واحدة. المقتطف ≤200 حرف وبلا رابط ويبهوك (أمان).
    ``price`` = السعر المُرسَل فعلاً و``comp_price`` = سعر المنافس المرجع —
    اختياريان (غيابهما ⇒ NULL لا 0). آمن: أي خطأ يُبتلع بتحذير — لا يمنع الإرسال."""
    try:
        excerpt = re.sub(r"https?://\S*hook\S*", "", str(response_excerpt or "")).strip()[:200]
        conn = _db(db_path)
        conn.execute(
            "INSERT INTO send_log (timestamp, batch_id, webhook_type, sku,"
            " product_name, category, brand, http_status, success, response_excerpt,"
            " price, comp_price)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), str(batch_id),
             str(webhook_type), str(sku), str(product_name), str(category),
             str(brand), int(http_status or 0), 1 if success else 0, excerpt,
             _fnum(price), _fnum(comp_price)))
        conn.commit(); conn.close()
    except Exception as e:
        logger.warning("تعذّر تدوين الإرسال في send_log: %s", e)


def is_duplicate_new_sku(sku, db_path=None) -> bool:
    """True إذا سبق إرسال هذا الـSKU كمنتج جديد بنجاح، أو وُجد في our_catalog.
    آمن: SKU فارغ أو أي خطأ ⇒ False (لا نمنع الإرسال بالشك)."""
    sku = str(sku or "").strip()
    if not sku:
        return False
    try:
        conn = _db(db_path)
        hit = conn.execute("SELECT 1 FROM send_log WHERE sku=? AND webhook_type='new'"
                           " AND success=1 LIMIT 1", (sku,)).fetchone()
        if hit is None:
            try:
                hit = conn.execute("SELECT 1 FROM our_catalog WHERE product_id=? LIMIT 1",
                                   (sku,)).fetchone()
            except Exception:
                hit = None  # الجدول غائب في هذه القاعدة — تجاهل بأمان
        conn.close()
        return hit is not None
    except Exception as e:
        logger.warning("تعذّر فحص تكرار الـSKU «%s»: %s", str(sku)[:40], e)
        return False


init_send_log()
