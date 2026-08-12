"""engines/__init__.py — تجميع المحركات وتسجيلها في السجل الموحد
══════════════════════════════════════════════════════════════════════════════"""
from __future__ import annotations

import logging
from typing import Dict, Type

from engines.unified_competitor_scraper import BaseCompetitorScraper, SCRAPER_REGISTRY

# استيراد المحركات لضمان تسجيلها في السجل
from engines.unified_competitor_scraper import (
    NoonScraper, AmazonSAScraper, JarirScraper, ExtraScraper, UnifiedScraper
)
from engines.perfume_scraper import (
    OudAndAttarScraper, NichePerfumeScraper, PerfumeClassifier, PerfumeProduct
)
from engines.salla_scraper import SallaStoreScraper
from engines.global_scraper import (
    AmazonScraper, FragranceNetScraper, NotinoScraper
)
from engines.price_monitor import (
    PriceMonitor, PriceMonitorDatabase, PriceChangeDetector,
    NotificationChannel, ConsoleNotification, FileNotification, DiscordNotification
)

logger = logging.getLogger("engines")

__all__ = [
    # Base & unified
    "BaseCompetitorScraper",
    "UnifiedScraper",
    "SCRAPER_REGISTRY",
    # Saudi competitors
    "NoonScraper",
    "AmazonSAScraper",
    "JarirScraper",
    "ExtraScraper",
    # Perfume specialist
    "PerfumeClassifier",
    "PerfumeProduct",
    "OudAndAttarScraper",
    "NichePerfumeScraper",
    # Salla platform
    "SallaStoreScraper",
    # Global stores
    "AmazonScraper",
    "FragranceNetScraper",
    "NotinoScraper",
    # Price monitoring
    "PriceMonitor",
    "PriceMonitorDatabase",
    "PriceChangeDetector",
    "NotificationChannel",
    "ConsoleNotification",
    "FileNotification",
    "DiscordNotification",
]


def get_all_scraper_sources() -> Dict[str, str]:
    """استرجاع قائمة بجميع المصادر المسجلة."""
    return {
        source: scraper.__name__
        for source, scraper in SCRAPER_REGISTRY.items()
    }


def create_scraper(source: str, **kwargs) -> BaseCompetitorScraper:
    """إنشاء محرك كشط بالاسم."""
    if source not in SCRAPER_REGISTRY:
        raise ValueError(f"Unknown scraper source: {source}. Available: {list(SCRAPER_REGISTRY.keys())}")
    return SCRAPER_REGISTRY[source](**kwargs)


if __name__ == "__main__":
    print("Registered scraper sources:")
    for src, cls_name in get_all_scraper_sources().items():
        print(f"  - {src}: {cls_name}")
