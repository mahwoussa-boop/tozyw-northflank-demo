"""infrastructure/db_manager.py — مدير اتصال SQLite (WAL + معاملات آمنة).

يوفّر طبقة وصول منخفضة المستوى لقاعدة بيانات SQLite الحيّة:
- اتصال لكل خيط (thread-local) مع ``check_same_thread=False`` لأمان Streamlit.
- وضع WAL وضبط PRAGMA للأداء مع 100K+ صف.
- مدير سياق ``transaction`` للالتزام/التراجع الذرّي.

⚠️ لا يعرّف هذا المدير أيّ جدول (schema). القاعدة الصارمة: ممنوع تغيير
مخطط SQLite الحيّ — الجداول تُنشأ وتُهاجَر في ``utils/db_manager.py`` القديم.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
from contextlib import contextmanager
from typing import Any, Iterable, Iterator, Optional, Sequence

from core.exceptions import RepositoryError

_Params = Sequence[Any]

logger = logging.getLogger(__name__)


class DatabaseManager:
    """يدير اتصالات SQLite بأمان عبر الخيوط مع تجميع لكل خيط."""

    def __init__(
        self,
        db_path: str,
        *,
        timeout: float = 30.0,
        wal: bool = True,
    ) -> None:
        self._db_path: str = str(db_path)
        self._timeout: float = timeout
        self._wal: bool = wal
        self._local = threading.local()
        self._lock = threading.Lock()
        # تتبع كل الاتصالات المُنشأة لتنظيف آمن عند التدمير (تفادي ResourceWarning).
        self._all_conns: list[sqlite3.Connection] = []
        self._conns_lock = threading.Lock()

    @property
    def db_path(self) -> str:
        return self._db_path

    def _new_connection(self) -> sqlite3.Connection:
        """ينشئ اتصالاً جديداً ويضبط PRAGMA الأداء."""
        try:
            conn = sqlite3.connect(
                self._db_path,
                timeout=self._timeout,
                check_same_thread=False,
            )
        except sqlite3.Error as exc:
            raise RepositoryError(
                "تعذّر فتح قاعدة البيانات", db_path=self._db_path, error=str(exc),
            ) from exc
        conn.row_factory = sqlite3.Row
        if self._wal:
            try:
                conn.execute("PRAGMA journal_mode=WAL")
            except sqlite3.OperationalError as exc:
                # قاعدة على قرص شبكي/للقراءة فقط قد ترفض WAL — نتابع بوضع اليومية الافتراضي
                logger.warning(
                    "تعذّر تفعيل WAL لقاعدة %s — متابعة بدون WAL: %s",
                    self._db_path, exc,
                )
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        with self._conns_lock:
            self._all_conns.append(conn)
        return conn

    def connection(self) -> sqlite3.Connection:
        """يعيد اتصال الخيط الحالي (ينشئه عند أول استخدام)."""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            with self._lock:
                # تحقق ثانٍ داخل القفل لتجنب race condition
                conn = getattr(self._local, "conn", None)
                if conn is None:
                    conn = self._new_connection()
                    self._local.conn = conn
        return conn

    def is_connection_closed(self) -> bool:
        """هل اتصال الخيط الحالي مغلق أو غير موجود؟"""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            return True
        try:
            conn.execute("SELECT 1")
            return False
        except sqlite3.ProgrammingError:
            return True

    def reset_connection(self) -> sqlite3.Connection:
        """يغلق اتصال الخيط الحالي (إن وُجد) ويُنشئ واحداً جديداً."""
        self.close()
        return self.connection()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """مدير سياق ذرّي: التزام عند النجاح، تراجع عند أيّ خطأ."""
        conn = self.connection()
        try:
            yield conn
            conn.commit()
        except Exception as exc:
            try:
                conn.rollback()
            except sqlite3.ProgrammingError:
                # الاتصال مغلق — لا شيء نستطيع فعله
                pass
            raise RepositoryError("فشلت المعاملة وتمّ التراجع", error=str(exc)) from exc

    def execute(self, sql: str, params: _Params = ()) -> sqlite3.Cursor:
        """ينفّذ جملة واحدة ويلتزم بها مباشرةً."""
        with self.transaction() as conn:
            return conn.execute(sql, params)

    def executemany(self, sql: str, seq_params: Iterable[_Params]) -> sqlite3.Cursor:
        """ينفّذ الجملة على مجموعة معاملات (إدراج/تحديث دفعي)."""
        with self.transaction() as conn:
            return conn.executemany(sql, list(seq_params))

    def query(self, sql: str, params: _Params = ()) -> list[sqlite3.Row]:
        """يعيد كل الصفوف المطابقة (للقراءة فقط)."""
        return self.connection().execute(sql, params).fetchall()

    def query_one(self, sql: str, params: _Params = ()) -> Optional[sqlite3.Row]:
        """يعيد أول صف مطابق أو ``None``."""
        return self.connection().execute(sql, params).fetchone()

    def table_exists(self, name: str) -> bool:
        """هل الجدول موجود؟ (يُستخدم قبل القراءة لتفادي الأخطاء)."""
        row = self.query_one(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (name,),
        )
        return row is not None

    def close(self) -> None:
        """يغلق جميع الاتصالات المفتوحة (بغضّ النظر عن الخيط) لتفادي ResourceWarning."""
        with self._conns_lock:
            for conn in self._all_conns:
                try:
                    conn.close()
                except sqlite3.ProgrammingError:
                    pass
            self._all_conns.clear()
        self._local.conn = None

    def __del__(self) -> None:
        """تنظيف عند التدمير: يغلق كل الاتصالات لتفادي ResourceWarning."""
        self.close()
