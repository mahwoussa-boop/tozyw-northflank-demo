"""services/intelligence/seasonal_engine.py — محرك التسعير الموسمي.

يدير تقويم المواسم التجارية في السعودية والشرق الأوسط ويوفر:
- اكتشاف المواسم النشطة والقادمة
- تنبيهات عربية للمواسم القادمة
- تعديل الأسعار تلقائياً حسب الموسم والفئة
- عرض المواسم خلال فترة زمنية محددة

المواسم المدعومة: رمضان، عيد الفطر، عيد الأضحى، الجمعة البيضاء،
العودة للمدارس، تخفيضات الصيف، اليوم الوطني، عيد الحب.

⚠️ لا يستورد Streamlit أبداً. خدمة بلا حالة (stateless).
"""
from __future__ import annotations

from core.product_entity import _utcnow

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Optional


# ════════════════════════════════════════════════════════════════════
#  نماذج البيانات
# ════════════════════════════════════════════════════════════════════

@dataclass
class SeasonInfo:
    """معلومات موسم تجاري واحد.

    Attributes:
        name: اسم الموسم بالعربية.
        active: هل الموسم نشط حالياً.
        days_until: عدد الأيام حتى بداية الموسم (0 إذا نشط).
        expected_impact_pct: نسبة التأثير المتوقع على الأسعار (%).
        affected_categories: الفئات المتأثرة.
        description: وصف مختصر بالعربية.
    """

    name: str = ""
    active: bool = False
    days_until: int = 0
    expected_impact_pct: float = 0.0
    affected_categories: list[str] = field(default_factory=list)
    description: str = ""


@dataclass
class SeasonalAlert:
    """تنبيه موسمي يُرسَل للمستخدم.

    Attributes:
        alert_id: معرّف فريد للتنبيه.
        season: اسم الموسم.
        title: عنوان التنبيه.
        body: نص التنبيه بالعربية.
        severity: مستوى الخطورة (info | warning).
        days_until: عدد الأيام حتى الموسم.
        expected_impact_pct: نسبة التأثير المتوقع.
        created_at: تاريخ إنشاء التنبيه.
    """

    alert_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    season: str = ""
    title: str = ""
    body: str = ""
    severity: str = "info"
    days_until: int = 0
    expected_impact_pct: float = 0.0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None))


# ════════════════════════════════════════════════════════════════════
#  محرك التسعير الموسمي
# ════════════════════════════════════════════════════════════════════

class SeasonalPricingEngine:
    """محرك التسعير الموسمي — يكتشف المواسم ويعدّل الأسعار.

    خدمة بلا حالة (stateless) تستخدم تقويم داخلي للمواسم التجارية
    في السعودية والشرق الأوسط. تدعم اكتشاف المواسم النشطة والقادمة،
    وتوليد تنبيهات عربية، وتعديل الأسعار حسب الموسم والفئة.
    """

    # ── تقويم المواسم التجارية ──────────────────────────────────
    # ملاحظة: التواريخ الهجرية (رمضان، الأعياد) تقريبية وتتغير سنوياً.
    # نستخدم تقديرات ميلادية لعام 2026 مع هامش مرونة.

    _SEASONS: list[dict] = [
        {
            "name": "رمضان",
            "start_month": 2,
            "start_day": 18,
            "end_month": 3,
            "end_day": 19,
            "impact_pct": 15.0,
            "categories": ["perfumes", "cosmetics", "عطور", "مستحضرات تجميل"],
            "description": "شهر رمضان المبارك — ارتفاع الطلب على العطور ومستحضرات التجميل",
            "severity": "warning",
        },
        {
            "name": "عيد الفطر",
            "start_month": 3,
            "start_day": 20,
            "end_month": 3,
            "end_day": 26,
            "impact_pct": 15.0,
            "categories": ["perfumes", "عطور", "هدايا"],
            "description": "عيد الفطر المبارك — ذروة الطلب على العطور والهدايا",
            "severity": "warning",
        },
        {
            "name": "عيد الأضحى",
            "start_month": 5,
            "start_day": 26,
            "end_month": 6,
            "end_day": 1,
            "impact_pct": 10.0,
            "categories": ["perfumes", "عطور", "هدايا"],
            "description": "عيد الأضحى المبارك — ارتفاع الطلب على العطور",
            "severity": "warning",
        },
        {
            "name": "الجمعة البيضاء",
            "start_month": 11,
            "start_day": 20,
            "end_month": 11,
            "end_day": 30,
            "impact_pct": -20.0,
            "categories": ["all", "perfumes", "cosmetics", "عطور", "مستحضرات تجميل", "الكل"],
            "description": "الجمعة البيضاء (Black Friday) — تخفيضات كبيرة على جميع الفئات",
            "severity": "info",
        },
        {
            "name": "العودة للمدارس",
            "start_month": 8,
            "start_day": 15,
            "end_month": 9,
            "end_day": 15,
            "impact_pct": 5.0,
            "categories": ["care", "منتجات العناية", "مستحضرات تجميل"],
            "description": "موسم العودة للمدارس — زيادة الطلب على منتجات العناية",
            "severity": "info",
        },
        {
            "name": "تخفيضات الصيف",
            "start_month": 6,
            "start_day": 1,
            "end_month": 7,
            "end_day": 31,
            "impact_pct": -10.0,
            "categories": ["all", "perfumes", "cosmetics", "عطور", "مستحضرات تجميل", "الكل"],
            "description": "تخفيضات الصيف — تخفيضات موسمية على معظم الفئات",
            "severity": "info",
        },
        {
            "name": "اليوم الوطني",
            "start_month": 9,
            "start_day": 20,
            "end_month": 9,
            "end_day": 25,
            "impact_pct": 5.0,
            "categories": ["perfumes", "عطور"],
            "description": "اليوم الوطني السعودي (23 سبتمبر) — عروض وطلب مرتفع",
            "severity": "info",
        },
        {
            "name": "عيد الحب",
            "start_month": 2,
            "start_day": 7,
            "end_month": 2,
            "end_day": 16,
            "impact_pct": 15.0,
            "categories": ["perfumes", "عطور", "هدايا"],
            "description": "عيد الحب (14 فبراير) — ذروة الطلب على العطور والهدايا",
            "severity": "warning",
        },
    ]

    def __init__(self) -> None:
        """تهيئة المحرك الموسمي — بلا حالة، يستخدم التقويم الداخلي."""
        pass

    # ── دوال مساعدة داخلية ──────────────────────────────────────

    def _get_season_dates(
        self,
        season: dict,
        ref_year: int,
    ) -> tuple[date, date]:
        """يحسب تاريخ البداية والنهاية لموسم في سنة معينة.

        Args:
            season: قاموس بيانات الموسم.
            ref_year: السنة المرجعية.

        Returns:
            (start_date, end_date)
        """
        start = date(ref_year, season["start_month"], season["start_day"])
        end = date(ref_year, season["end_month"], season["end_day"])
        return start, end

    def _check_season(
        self,
        season: dict,
        current: date,
    ) -> Optional[SeasonInfo]:
        """يفحص موسماً واحداً ويعيد معلوماته إذا كان نشطاً أو خلال 30 يوم.

        Args:
            season: قاموس بيانات الموسم.
            current: التاريخ الحالي.

        Returns:
            SeasonInfo إذا كان الموسم ضمن النطاق، أو None.
        """
        # نفحص الموسم في السنة الحالية والسنة القادمة
        for year_offset in (0, 1):
            year = current.year + year_offset
            try:
                start, end = self._get_season_dates(season, year)
            except ValueError:
                continue

            # الموسم نشط حالياً
            if start <= current <= end:
                return SeasonInfo(
                    name=season["name"],
                    active=True,
                    days_until=0,
                    expected_impact_pct=season["impact_pct"],
                    affected_categories=list(season["categories"]),
                    description=season["description"],
                )

            # الموسم قادم خلال 30 يوم
            days = (start - current).days
            if 0 < days <= 30:
                return SeasonInfo(
                    name=season["name"],
                    active=False,
                    days_until=days,
                    expected_impact_pct=season["impact_pct"],
                    affected_categories=list(season["categories"]),
                    description=season["description"],
                )

        return None

    # ── اكتشاف المواسم ──────────────────────────────────────────

    def detect_season(
        self,
        *,
        current_date: Optional[date] = None,
    ) -> list[SeasonInfo]:
        """يكتشف المواسم النشطة أو القادمة خلال 30 يوم.

        يفحص كل المواسم في التقويم ويعيد المواسم ضمن النطاق
        مرتبة حسب الأقرب أولاً (days_until تصاعدياً).

        Args:
            current_date: التاريخ الحالي (للاختبار). إذا None يستخدم اليوم.

        Returns:
            قائمة SeasonInfo مرتبة حسب القرب.
        """
        today = current_date or date.today()
        results: list[SeasonInfo] = []

        for season in self._SEASONS:
            info = self._check_season(season, today)
            if info is not None:
                results.append(info)

        # ترتيب: النشط أولاً (days_until=0)، ثم الأقرب
        results.sort(key=lambda s: s.days_until)
        return results

    # ── تنبيهات موسمية ──────────────────────────────────────────

    def get_seasonal_alerts(
        self,
        *,
        current_date: Optional[date] = None,
    ) -> list[SeasonalAlert]:
        """يولّد تنبيهات عربية للمواسم القادمة والنشطة.

        ينشئ تنبيهات واضحة بالعربية مع رموز تعبيرية ومعلومات التأثير.

        Args:
            current_date: التاريخ الحالي (للاختبار).

        Returns:
            قائمة SeasonalAlert مرتبة حسب القرب.
        """
        seasons = self.detect_season(current_date=current_date)
        alerts: list[SeasonalAlert] = []

        for info in seasons:
            if info.active:
                icon = "🔴"
                title = f"{icon} {info.name} — نشط الآن"
                impact_dir = "ارتفاع" if info.expected_impact_pct > 0 else "انخفاض"
                body = (
                    f"{icon} {info.name} نشط حالياً — "
                    f"توقع {impact_dir} أسعار "
                    f"{', '.join(info.affected_categories[:2])} "
                    f"بنسبة {abs(info.expected_impact_pct):.0f}%"
                )
                severity = "warning"
            else:
                icon = "⚠️"
                title = f"{icon} {info.name} خلال {info.days_until} يوم"
                impact_dir = "ارتفاع" if info.expected_impact_pct > 0 else "انخفاض"
                body = (
                    f"{icon} {info.name} خلال {info.days_until} يوم — "
                    f"توقع {impact_dir} أسعار "
                    f"{', '.join(info.affected_categories[:2])} "
                    f"بنسبة {abs(info.expected_impact_pct):.0f}%"
                )
                severity = "info" if info.days_until > 14 else "warning"

            alerts.append(SeasonalAlert(
                season=info.name,
                title=title,
                body=body,
                severity=severity,
                days_until=info.days_until,
                expected_impact_pct=info.expected_impact_pct,
            ))

        return alerts

    # ── تعديل السعر حسب الموسم ──────────────────────────────────

    def adjust_price_for_season(
        self,
        base_price: float,
        *,
        category: str = "perfumes",
        current_date: Optional[date] = None,
    ) -> tuple[float, str]:
        """يعدّل السعر حسب الموسم النشط والفئة.

        إذا كان هناك موسم نشط يؤثر على الفئة المحددة، يُعدَّل السعر
        بنسبة التأثير المتوقع. إذا لم يكن هناك موسم نشط، يُعاد السعر بلا تغيير.

        Args:
            base_price: السعر الأساسي.
            category: فئة المنتج (مثل "perfumes", "cosmetics").
            current_date: التاريخ الحالي (للاختبار).

        Returns:
            (السعر_المعدّل, سبب_التعديل). إذا لم يُعدّل: (السعر_الأصلي, "").
        """
        seasons = self.detect_season(current_date=current_date)
        cat_lower = category.lower().strip()

        for info in seasons:
            if not info.active:
                continue

            # فحص إذا كانت الفئة متأثرة
            affected = [c.lower() for c in info.affected_categories]
            if cat_lower in affected or "all" in affected or "الكل" in affected:
                adjustment = info.expected_impact_pct / 100.0
                adjusted_price = base_price * (1.0 + adjustment)
                adjusted_price = round(max(0.0, adjusted_price), 2)

                reason = (
                    f"تعديل موسمي ({info.name}): "
                    f"{'+' if info.expected_impact_pct > 0 else ''}"
                    f"{info.expected_impact_pct:.0f}% "
                    f"على فئة {category}"
                )
                return adjusted_price, reason

        return base_price, ""

    # ── المواسم القادمة ─────────────────────────────────────────

    def get_upcoming_seasons(
        self,
        *,
        days_ahead: int = 90,
        current_date: Optional[date] = None,
    ) -> list[SeasonInfo]:
        """يعرض المواسم خلال فترة زمنية محددة.

        يفحص كل المواسم ويعيد تلك التي تبدأ خلال ``days_ahead`` يوم
        (تشمل المواسم النشطة أيضاً).

        Args:
            days_ahead: عدد الأيام للبحث (الافتراضي: 90).
            current_date: التاريخ الحالي (للاختبار).

        Returns:
            قائمة SeasonInfo مرتبة حسب القرب.
        """
        today = current_date or date.today()
        results: list[SeasonInfo] = []

        for season in self._SEASONS:
            for year_offset in (0, 1):
                year = today.year + year_offset
                try:
                    start, end = self._get_season_dates(season, year)
                except ValueError:
                    continue

                # الموسم نشط حالياً
                if start <= today <= end:
                    results.append(SeasonInfo(
                        name=season["name"],
                        active=True,
                        days_until=0,
                        expected_impact_pct=season["impact_pct"],
                        affected_categories=list(season["categories"]),
                        description=season["description"],
                    ))
                    break  # لا نكرر لنفس الموسم

                # الموسم قادم خلال days_ahead
                days = (start - today).days
                if 0 < days <= days_ahead:
                    results.append(SeasonInfo(
                        name=season["name"],
                        active=False,
                        days_until=days,
                        expected_impact_pct=season["impact_pct"],
                        affected_categories=list(season["categories"]),
                        description=season["description"],
                    ))
                    break  # لا نكرر لنفس الموسم

        results.sort(key=lambda s: s.days_until)
        return results
