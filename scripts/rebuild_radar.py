# -*- coding: utf-8 -*-
"""scripts/rebuild_radar.py — تحديث طزاجة رادار الفرص بعد كشط الليل الخارجي.

المشكلة (كشف الجلسة 11): جولة الكشط الليلية (~04:01) يديرها كاشطٌ خارجي قديم يكتب
مباشرةً في ``pricing_v18.db`` **بلا المرور بخطّاف إعادة البناء داخل التطبيق** — فيبقى
جدول ``opportunity_scores`` (كاش الرادار المشتق) متأخّراً يوماً كاملاً حتى يفتح المالك
التطبيق ويكشط. هذا السكربت يُشغَّل بمهمة مجدولة ~04:30 (بعد فراغ الكاشط) فيعيد بناء
الكاش من أحدث بيانات المنافسين.

آمن تماماً:
  • يقرأ فقط من ``competitor_products_store`` (المصدر) ويكتب الكاش المشتق فقط.
  • ``rebuild_opportunity_scores`` **ذرّي**: تبديل مخطّط DROP+CREATE+فهرسة+INSERT
    داخل معاملة واحدة (BEGIN صريح) — أي فشل ⇒ تراجع كامل ⇒ الكاش القديم يبقى سليماً
    (لا حالة نصفية). لا يمسّ أي سجلّ/أرشيف/سعر/إرسال.
  • ``--check`` وضع قراءة-فقط: يطبع الحالة الحالية بلا أي كتابة.

يُسجَّل الناتج (طابع زمني + عدد الصفوف + rebuilt_at قبل/بعد) لتشخيص أي فشل صامت.
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime
from pathlib import Path

# اجعل الطباعة آمنة الترميز مهما كان ترميز الطرفية/الوجهة: على console الويندوز
# (cp1256) لا تُرمَّز الرموز ✅/⚠️ (U+2705/U+26A0) فيرمي ``print`` UnicodeEncodeError
# ويخرج السكربت 1 (فشل كاذب) رغم أن الكاش أُودِع ذرّياً قبلها. reconfigure يزيل الفخّ.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# جذر المشروع على المسار حتى يعمل السكربت من أي مجلد (المهمة المجدولة).
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.data_paths import get_data_db_path  # noqa: E402


def _state(db_path: str) -> tuple[int, str]:
    """(عدد الصفوف، أحدث rebuilt_at) من الكاش — قراءة-فقط. خطأ ⇒ (0, '')."""
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            n = con.execute("SELECT COUNT(*) FROM opportunity_scores").fetchone()[0]
            ts = con.execute(
                "SELECT MAX(rebuilt_at) FROM opportunity_scores").fetchone()[0] or ""
            return int(n), str(ts)
        finally:
            con.close()
    except Exception:
        return 0, ""


def main(check_only: bool = False) -> int:
    stamp = datetime.now().isoformat(timespec="seconds")
    db_path = get_data_db_path("pricing_v18.db")
    before_n, before_ts = _state(db_path)
    print(f"[{stamp}] rebuild_radar | db={db_path}")
    print(f"  قبل: {before_n} صفّاً · rebuilt_at={before_ts or '—'}")

    if check_only:
        print("  (--check: قراءة-فقط، لا كتابة)")
        return 0

    from services.opportunity_service import rebuild_opportunity_scores
    n = rebuild_opportunity_scores(db_path)
    after_n, after_ts = _state(db_path)
    print(f"  بعد: {after_n} صفّاً · rebuilt_at={after_ts or '—'} · أُرجِع={n}")
    if n <= 0:
        print("  ⚠️ أُرجِع 0 — الكاش القديم محفوظ (تراجع ذرّي). راجِع السجل.")
        return 1

    # كاش أولوية «سعر أعلى» — ثانوي، لا يُفشل السكربت (الرادار هو الغرض الأساسي هنا).
    try:
        from services.priority_engine import rebuild_product_priority_scores
        _np = rebuild_product_priority_scores(db_path)
        print(f"  ⚡ كاش أولوية سعر أعلى: أُرجِع={_np}")
    except Exception as exc:
        print(f"  ⚠️ كاش الأولوية تعذّر (تجاهل، ثانوي): {exc}")

    print("  ✅ اكتمل تحديث الرادار.")
    return 0


if __name__ == "__main__":
    sys.exit(main(check_only="--check" in sys.argv[1:]))
