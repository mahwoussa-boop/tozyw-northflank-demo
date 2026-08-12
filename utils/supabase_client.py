"""utils/supabase_client.py — عميل Supabase خفيف (مرآة «مصدر الحقيقة: سلة»).

يُستخدم **فقط** للكتابة (upsert) من ``sync_store_maps`` إلى جدولَي
``store_brands``/``store_categories`` (on conflict ``salla_id``). القراءة الحارّة
تبقى على CSV (سريعة/أوفلاين) — لا تمرّ من هنا.

اختياري بالكامل: بلا ``SUPABASE_URL``/``SUPABASE_SERVICE_KEY`` أو بلا حزمة
``supabase`` ⇒ تُعيد الدوال نتيجة محايدة دون رفع استثناء (لا يكسر المزامنة).
"""
from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger("SupabaseClient")


def _url() -> str:
    return os.environ.get("SUPABASE_URL", "").strip()


def _key() -> str:
    return (
        os.environ.get("SUPABASE_SERVICE_KEY", "")
        or os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    ).strip()


def is_configured() -> bool:
    """True إذا توفّر عنوان Supabase + مفتاح الخدمة — وإلا لا upsert."""
    return bool(_url() and _key())


_CLIENT_CACHE: list = []  # [client] أو [None] — كاش كسول


def _client():
    """عميل Supabase (كسول، مع كاش). None إن لم يُهيّأ أو غابت الحزمة."""
    if _CLIENT_CACHE:
        return _CLIENT_CACHE[0]
    client = None
    if is_configured():
        try:
            from supabase import create_client  # type: ignore
            client = create_client(_url(), _key())
        except Exception as e:  # حزمة غير مثبّتة أو خطأ تهيئة
            logger.warning("تعذّر إنشاء عميل Supabase (تأكّد من حزمة supabase): %s", e)
    _CLIENT_CACHE.append(client)
    return client


def upsert_store_maps(brands: list, cats: list, _client=None) -> int:
    """يكتب (upsert) الماركات/التصنيفات إلى Supabase على conflict ``salla_id``.

    ``brands``/``cats``: ``[{name, id}]`` (مخرجات fetch_brands/fetch_categories).
    ``_client`` قابل للحقن (اختبار). يعيد عدد الصفوف المُرسَلة. آمن: بلا تهيئة/خطأ ⇒ 0.
    """
    client = _client if _client is not None else _client_or_none()
    if client is None:
        return 0
    n = 0
    try:
        if brands:
            # مخطّط الماركات يستخدم name_ar (وname_norm مولّد منه في Postgres)؛
            # لا عمود name. ندعم «عربي | English» ⇒ name_ar/name_en.
            rows = [_brand_row(b) for b in brands]
            client.table("store_brands").upsert(rows, on_conflict="salla_id").execute()
            n += len(rows)
        if cats:
            # مخطّط التصنيفات فيه name.
            rows = [{"salla_id": c["id"], "name": c["name"]} for c in cats]
            client.table("store_categories").upsert(rows, on_conflict="salla_id").execute()
            n += len(rows)
    except Exception as e:
        logger.warning("Supabase upsert تعذّر: %s", e)
    return n


def _brand_row(b: dict) -> dict:
    """صفّ ماركة لـ Supabase: ``name_ar`` (+``name_en`` إن كان الاسم «عربي | English»)."""
    nm = str(b.get("name") or "").strip()
    row = {"salla_id": b["id"]}
    if "|" in nm:
        ar, _, en = nm.partition("|")
        row["name_ar"] = ar.strip()
        en = en.strip()
        if en:
            row["name_en"] = en
    else:
        row["name_ar"] = nm
    return row


def _client_or_none():
    """غلاف آمن حول ``_client`` (يبتلع أي خطأ تهيئة)."""
    try:
        return _client()
    except Exception:
        return None
