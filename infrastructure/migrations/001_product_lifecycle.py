"""infrastructure/migrations/001_product_lifecycle.py — هجرة إنشاء جداول كيان المنتج والأحداث.

تنشئ جدولين:
- ``product_entities``: يخزّن حالة كل منتج ومعلوماته الأساسية.
- ``product_events``: يسجّل كل حدث في دورة حياة المنتج (Event Sourcing خفيف).

كل جدول يُنشأ بـ ``CREATE TABLE IF NOT EXISTS`` لتجنّب الخطأ عند التشغيل المتكرر.
"""
from __future__ import annotations

import sqlite3


def upgrade(conn: sqlite3.Connection) -> None:
    """إنشاء جداول product_entities و product_events مع الفهارس اللازمة."""

    conn.execute("""
        CREATE TABLE IF NOT EXISTS product_entities (
            entity_id        TEXT PRIMARY KEY,
            product_id       TEXT NOT NULL,
            name             TEXT NOT NULL,
            brand            TEXT,
            state            TEXT NOT NULL DEFAULT 'discovered',
            current_price    REAL DEFAULT 0.0,
            suggested_price  REAL,
            section          TEXT,
            ai_confidence    REAL DEFAULT 0.0,
            competitor_prices TEXT DEFAULT '{}',
            created_at       TEXT NOT NULL,
            updated_at       TEXT NOT NULL
        )
    """)

    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_pe_product_id
        ON product_entities (product_id)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_pe_state
        ON product_entities (state)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_pe_updated
        ON product_entities (updated_at)
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS product_events (
            event_id    TEXT PRIMARY KEY,
            entity_id   TEXT NOT NULL,
            timestamp   TEXT NOT NULL,
            event_type  TEXT NOT NULL,
            old_state   TEXT,
            new_state   TEXT,
            actor       TEXT DEFAULT 'system',
            data        TEXT DEFAULT '{}',
            metadata    TEXT DEFAULT '{}',
            created_at  TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (entity_id) REFERENCES product_entities (entity_id)
        )
    """)

    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_pev_entity
        ON product_events (entity_id)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_pev_type
        ON product_events (event_type)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_pev_ts
        ON product_events (timestamp)
    """)

    conn.commit()


def downgrade(conn: sqlite3.Connection) -> None:
    """حذف جداول product_events و product_entities (بالترتيب العكسي لتجنّب خطأ المفاتيح الأجنبية)."""

    conn.execute("DROP TABLE IF EXISTS product_events")
    conn.execute("DROP TABLE IF EXISTS product_entities")
    conn.commit()
