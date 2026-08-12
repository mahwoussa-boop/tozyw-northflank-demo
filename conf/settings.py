"""config/settings.py — إعدادات النظام بنموذج Pydantic v2 مجمّد.

تُقرأ المفاتيح أولاً من ``os.environ`` ثم من ``st.secrets`` عند التوفر —
مطابق لمنطق ``config.py::_s`` و``_parse_gemini_keys``.

ملاحظة: لا نعتمد ``pydantic-settings`` (غير مثبّت في البيئة)؛ نستخدم
``BaseModel`` + محمّل بيئة صريح كي لا ينكسر الاستيراد.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from conf.constants import DEFAULT_DB_PATH, PROJECT_ROOT


def _load_dotenv() -> None:
    """يحمّل ``.env`` من جذر المستودع إلى ``os.environ`` عند بدء التشغيل.

    لا يطغى على متغيّرات بيئة موجودة (``setdefault``/``override=False``) كي
    تبقى أسرار Railway/النظام لها الأولوية. يستخدم ``python-dotenv`` إن توفّر،
    وإلا يقرأ الملف يدوياً (سطور ``KEY=VALUE`` مع تجاهل الفراغ والتعليقات).
    """
    env_file = PROJECT_ROOT / ".env"
    if not env_file.exists():
        return
    try:
        from dotenv import load_dotenv  # type: ignore

        load_dotenv(env_file, override=False)
        return
    except Exception:
        pass
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip())


_load_dotenv()

_GEMINI_MODEL = "gemini-2.5-flash"
_GEMINI_MODEL_DEEP = "gemini-2.5-pro"


def _secret(key: str, default: str = "") -> str:
    """يقرأ مفتاحاً: البيئة أولاً ثم ``st.secrets`` (#PRESERVED_LOGIC config._s)."""
    value = os.environ.get(key, "")
    if value:
        return value
    try:
        import streamlit as st  # استيراد كسول: قد لا يكون متاحاً خارج الواجهة

        secret_value = st.secrets[key]
        if secret_value is not None:
            return str(secret_value)
    except Exception:
        pass
    return default


def _parse_gemini_keys() -> list[str]:
    """يجمع مفاتيح Gemini من كل الصيغ المدعومة (#PRESERVED_LOGIC config:63-90)."""
    keys: list[str] = []
    raw = _secret("GEMINI_API_KEYS", "").strip()
    if raw.startswith("["):
        try:
            parsed = json.loads(raw)
            keys = [k for k in parsed if k] if isinstance(parsed, list) else []
        except Exception:
            clean = raw.strip("[]").replace('"', "").replace("'", "")
            keys = [k.strip() for k in clean.split(",") if k.strip()]
    elif raw:
        keys = [raw]
    single = _secret("GEMINI_API_KEY", "")
    if single and single not in keys:
        keys.append(single)
    for i in range(1, 51):  # يدعم تدوير حتى 50 مفتاحاً بصيغتي الترقيم
        for name in (f"GEMINI_API_KEY_{i}", f"GEMINI_KEY_{i}"):
            k = _secret(name, "")
            if k and k not in keys:
                keys.append(k)
    return [k.strip() for k in keys if k and len(k.strip()) > 20]


def _resolve_db_path() -> str:
    """يحلّ مسار قاعدة البيانات عبر مُحلّل المشروع القديم، وإلا الافتراضي."""
    env_path = os.environ.get("DB_PATH", "")
    if env_path:
        return env_path
    try:
        if str(PROJECT_ROOT) not in sys.path:
            sys.path.insert(0, str(PROJECT_ROOT))
        from utils.data_paths import get_data_db_path  # type: ignore

        return str(get_data_db_path("perfume_pricing.db"))
    except Exception:
        return str(DEFAULT_DB_PATH)


class Settings(BaseModel):
    """إعدادات النظام المجمّدة. تُبنى مرة واحدة عبر ``Settings.load()``."""

    model_config = ConfigDict(frozen=True)

    gemini_api_keys: list[str] = Field(default_factory=list)
    openrouter_api_key: str = ""
    cohere_api_key: str = ""
    webhook_update_prices: str = ""
    webhook_new_products: str = ""
    gemini_model: str = _GEMINI_MODEL
    gemini_model_deep: str = _GEMINI_MODEL_DEEP
    db_path: str = str(DEFAULT_DB_PATH)
    ai_batch_size: int = 8
    ai_max_retries: int = 3
    ai_timeout_s: int = 30
    ai_max_items: int = 150

    @property
    def any_ai_configured(self) -> bool:
        """هل أيّ مزوّد ذكاء اصطناعي مُهيّأ؟ (#PRESERVED_LOGIC config:101)."""
        return bool(
            self.gemini_api_keys
            or self.openrouter_api_key.strip()
            or self.cohere_api_key.strip()
        )

    @classmethod
    def load(cls) -> "Settings":
        """يبني الإعدادات من البيئة/الأسرار (المدخل الوحيد للتهيئة)."""
        kwargs: dict[str, Any] = {
            "gemini_api_keys": _parse_gemini_keys(),
            "openrouter_api_key": _secret("OPENROUTER_API_KEY")
            or _secret("OPENROUTER_KEY"),
            "cohere_api_key": _secret("COHERE_API_KEY"),
            "webhook_update_prices": _secret("WEBHOOK_UPDATE_PRICES")
            or os.environ.get("MAKE_WEBHOOK_URL", ""),
            "webhook_new_products": _secret("WEBHOOK_NEW_PRODUCTS")
            or os.environ.get("MAKE_WEBHOOK_URL_2", ""),
            "db_path": _resolve_db_path(),
        }
        return cls(**kwargs)
