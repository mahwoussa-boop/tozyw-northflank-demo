"""infrastructure/product_repository.py — مستودع كيان المنتج (ProductEntity).

يوفّر CRUD كامل + استعلامات متقدمة لكيانات المنتج وأحداثها.
يعتمد على ``DatabaseManager`` للوصول للقاعدة و ``001_product_lifecycle``
لضمان وجود الجداول.

القواعد:
- لا يستورد Streamlit أبداً.
- يُخزّن ``competitor_prices`` كـ JSON في عمود TEXT.
- يحمّل الأحداث عند استرجاع الكيان عبر ``get()``.
- كل عملية كتابة تستخدم ``db.execute()`` أو ``db.transaction()``.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Optional

import importlib as _importlib

from core.product_entity import ProductEntity, ProductEvent, ProductState
from infrastructure.db_manager import DatabaseManager

_migration = _importlib.import_module("infrastructure.migrations.001_product_lifecycle")
upgrade = _migration.upgrade


class ProductRepository:
    """مستودع كيانات المنتج — CRUD + استعلامات + أحداث.

    يُحقن ``DatabaseManager`` عبر المُنشئ (Dependency Injection).
    """

    def __init__(self, db: DatabaseManager) -> None:
        """يهيّئ المستودع مع مدير قاعدة بيانات.

        Args:
            db: مدير اتصال SQLite.
        """
        self._db = db

    # ── إنشاء الجداول ───────────────────────────────────────────

    def ensure_tables(self) -> None:
        """يضمن وجود جداول product_entities و product_events.

        يشغّل هجرة ``001_product_lifecycle.upgrade`` على الاتصال الحالي.
        آمن للتكرار بسبب ``CREATE TABLE IF NOT EXISTS``.
        """
        upgrade(self._db.connection())

    # ── إدراج / تحديث ────────────────────────────────────────────

    def upsert(self, entity: ProductEntity) -> str:
        """يدرج أو يحدّث كيان منتج في قاعدة البيانات.

        يُخزّن ``competitor_prices`` كسلسلة JSON. يُعيد ``entity_id``.

        Args:
            entity: كيان المنتج المراد حفظه.

        Returns:
            entity_id للكيان المُخزَّن.
        """
        sql = """
            INSERT OR REPLACE INTO product_entities
                (entity_id, product_id, name, brand, state, current_price,
                 suggested_price, section, ai_confidence, competitor_prices,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        params = (
            entity.entity_id,
            entity.product_id,
            entity.name,
            entity.brand,
            entity.state.value,
            entity.current_price,
            entity.suggested_price,
            entity.section,
            entity.ai_confidence,
            json.dumps(entity.competitor_prices, ensure_ascii=False),
            entity.created_at.isoformat(),
            entity.updated_at.isoformat(),
        )
        self._db.execute(sql, params)
        return entity.entity_id

    def upsert_batch(self, entities: list[ProductEntity]) -> int:
        """يدرج أو يحدّث دفعة من كيانات المنتج.

        يستخدم ``executemany`` لأداء أفضل. يُعيد عدد الكيانات المُعالَجة.

        Args:
            entities: قائمة كيانات المنتج.

        Returns:
            عدد الكيانات المُعالَجة.
        """
        if not entities:
            return 0

        sql = """
            INSERT OR REPLACE INTO product_entities
                (entity_id, product_id, name, brand, state, current_price,
                 suggested_price, section, ai_confidence, competitor_prices,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        params_list = [
            (
                e.entity_id,
                e.product_id,
                e.name,
                e.brand,
                e.state.value,
                e.current_price,
                e.suggested_price,
                e.section,
                e.ai_confidence,
                json.dumps(e.competitor_prices, ensure_ascii=False),
                e.created_at.isoformat(),
                e.updated_at.isoformat(),
            )
            for e in entities
        ]
        self._db.executemany(sql, params_list)
        return len(entities)

    # ── استرجاع ────────────────────────────────────────────────

    def get(self, entity_id: str) -> Optional[ProductEntity]:
        """يسترجع كيان منتج مع كل أحداثه.

        يحمّل صف الكيان من ``product_entities`` ثم يحمّل كل أحداثه
        من ``product_events`` ويضعها في ``entity.events``.

        Args:
            entity_id: معرّف الكيان.

        Returns:
            كيان المنتج أو ``None`` إن لم يُوجد.
        """
        row = self._db.query_one(
            "SELECT * FROM product_entities WHERE entity_id = ?",
            (entity_id,),
        )
        if row is None:
            return None

        entity = self._row_to_entity(row)
        entity.events = self.get_timeline(entity_id)
        return entity

    def find_by_product_id(self, product_id: str) -> list[ProductEntity]:
        """يبحث عن كيانات بمعرّف المنتج.

        Args:
            product_id: معرّف المنتج (قد يتكرر مع مصادر مختلفة).

        Returns:
            قائمة الكيانات المطابقة.
        """
        rows = self._db.query(
            "SELECT * FROM product_entities WHERE product_id = ?",
            (product_id,),
        )
        return [self._row_to_entity(r) for r in rows]

    def get_by_state(
        self, state: ProductState, *, limit: int = 100
    ) -> list[ProductEntity]:
        """يسترجع كيانات بحالة معيّنة.

        Args:
            state: الحالة المطلوبة.
            limit: الحد الأقصى للنتائج (افتراضي 100).

        Returns:
            قائمة الكيانات في تلك الحالة.
        """
        rows = self._db.query(
            "SELECT * FROM product_entities WHERE state = ? LIMIT ?",
            (state.value, limit),
        )
        return [self._row_to_entity(r) for r in rows]

    def get_stale(self, days: int = 7) -> list[ProductEntity]:
        """يسترجع المنتجات التي لم تُحدَّث منذ عدد أيام معيّن.

        Args:
            days: عدد الأيام (افتراضي 7).

        Returns:
            قائمة الكيانات القديمة (stale).
        """
        from core.product_entity import _utcnow
        cutoff = (_utcnow() - timedelta(days=days)).isoformat()
        rows = self._db.query(
            "SELECT * FROM product_entities WHERE updated_at < ?",
            (cutoff,),
        )
        return [self._row_to_entity(r) for r in rows]

    # ── الأحداث ───────────────────────────────────────────────

    def get_timeline(self, entity_id: str) -> list[ProductEvent]:
        """يسترجع كل أحداث كيان مرتّبة زمنياً.

        Args:
            entity_id: معرّف الكيان.

        Returns:
            قائمة الأحداث مرتّبة بالتوقيت تصاعدياً.
        """
        rows = self._db.query(
            "SELECT * FROM product_events WHERE entity_id = ? ORDER BY timestamp ASC",
            (entity_id,),
        )
        return [self._row_to_event(r) for r in rows]

    def record_event(self, entity_id: str, event: ProductEvent) -> None:
        """يسجّل حدثاً واحداً في جدول الأحداث.

        Args:
            entity_id: معرّف الكيان المرتبط.
            event: الحدث المراد تسجيله.
        """
        sql = """
            INSERT INTO product_events
                (event_id, entity_id, timestamp, event_type,
                 old_state, new_state, actor, data, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        params = (
            event.event_id,
            entity_id,
            event.timestamp.isoformat(),
            event.event_type if isinstance(event.event_type, str) else event.event_type.value,
            event.old_state,
            event.new_state,
            event.actor,
            json.dumps(event.data, ensure_ascii=False),
            json.dumps(event.metadata, ensure_ascii=False),
        )
        self._db.execute(sql, params)

    # ── إحصائيات ──────────────────────────────────────────────

    def count_by_state(self) -> dict[str, int]:
        """يحسب عدد المنتجات لكل حالة.

        Returns:
            قاموس {state_value: count}.
        """
        rows = self._db.query(
            "SELECT state, COUNT(*) as cnt FROM product_entities GROUP BY state"
        )
        return {row["state"]: row["cnt"] for row in rows}

    # ── مساعدات داخلية ────────────────────────────────────────

    @staticmethod
    def _row_to_entity(row: object) -> ProductEntity:
        """يحوّل صف SQLite إلى كيان ProductEntity.

        Args:
            row: صف sqlite3.Row.

        Returns:
            كيان ProductEntity.
        """
        d = dict(row)  # type: ignore[arg-type]
        # فك JSON لأسعار المنافسين
        cp_raw = d.get("competitor_prices", "{}")
        if isinstance(cp_raw, str):
            try:
                d["competitor_prices"] = json.loads(cp_raw)
            except (json.JSONDecodeError, TypeError):
                d["competitor_prices"] = {}
        return ProductEntity.from_dict(d)

    @staticmethod
    def _row_to_event(row: object) -> ProductEvent:
        """يحوّل صف SQLite إلى حدث ProductEvent.

        Args:
            row: صف sqlite3.Row.

        Returns:
            حدث ProductEvent.
        """
        d = dict(row)  # type: ignore[arg-type]
        # فك JSON لحقلي data و metadata
        for key in ("data", "metadata"):
            raw = d.get(key, "{}")
            if isinstance(raw, str):
                try:
                    d[key] = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    d[key] = {}
        return ProductEvent.from_dict(d)
