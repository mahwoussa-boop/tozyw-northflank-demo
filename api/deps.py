"""api/deps.py — حقن الاعتماديات لـ FastAPI.

يوفّر دالة ``get_container()`` التي تبني حاوية الاعتماديات مرة واحدة
(singleton) ويعيد استخدامها في كل طلب. يُستخدم كـ ``Depends(get_container)``
في نقاط الوصول.
"""
from __future__ import annotations

from typing import Optional

from bootstrap import Container, build_container

_container: Optional[Container] = None


def get_container() -> Container:
    """يُعيد حاوية الاعتماديات (يبنيها مرة واحدة فقط).

    Returns:
        كائن ``Container`` يحتوي جميع الخدمات المُهيّأة.
    """
    global _container
    if _container is None:
        _container = build_container()
    return _container
