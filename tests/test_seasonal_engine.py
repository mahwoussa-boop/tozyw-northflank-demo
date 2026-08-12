"""tests/test_seasonal_engine.py — اختبارات شاملة لمحرك التسعير الموسمي.

يغطي: اكتشاف المواسم، التنبيهات العربية، تعديل الأسعار، المواسم القادمة،
إنشاء النماذج، والمواسم المتزامنة.
"""
from __future__ import annotations

from datetime import date, datetime

import pytest

from services.intelligence.seasonal_engine import (
    SeasonalAlert,
    SeasonalPricingEngine,
    SeasonInfo,
)


# ════════════════════════════════════════════════════════════════════
#  Fixtures
# ════════════════════════════════════════════════════════════════════

@pytest.fixture()
def engine() -> SeasonalPricingEngine:
    """ينشئ محرك موسمي للاختبار."""
    return SeasonalPricingEngine()


# ════════════════════════════════════════════════════════════════════
#  اختبارات اكتشاف المواسم
# ════════════════════════════════════════════════════════════════════

class TestDetectSeason:
    """اختبارات detect_season()."""

    def test_detect_active_season_ramadan(self, engine: SeasonalPricingEngine) -> None:
        """اكتشاف رمضان كموسم نشط عند تاريخ خلاله."""
        # رمضان: 18 فبراير - 19 مارس
        d = date(2026, 3, 1)
        seasons = engine.detect_season(current_date=d)

        ramadan = [s for s in seasons if s.name == "رمضان"]
        assert len(ramadan) == 1
        assert ramadan[0].active is True
        assert ramadan[0].days_until == 0

    def test_detect_upcoming_season(self, engine: SeasonalPricingEngine) -> None:
        """اكتشاف موسم قادم خلال 30 يوم."""
        # قبل الجمعة البيضاء بـ 10 أيام (تبدأ 20 نوفمبر)
        d = date(2026, 11, 10)
        seasons = engine.detect_season(current_date=d)

        black_friday = [s for s in seasons if s.name == "الجمعة البيضاء"]
        assert len(black_friday) == 1
        assert black_friday[0].active is False
        assert black_friday[0].days_until == 10

    def test_detect_season_empty_when_none(self, engine: SeasonalPricingEngine) -> None:
        """لا مواسم نشطة أو قريبة → قائمة فارغة."""
        # 15 أكتوبر — بعيد عن كل المواسم (اليوم الوطني انتهى 25/9،
        # الجمعة البيضاء تبدأ 20/11 = بعد 36 يوم)
        d = date(2026, 10, 15)
        seasons = engine.detect_season(current_date=d)

        assert isinstance(seasons, list)
        assert len(seasons) == 0

    def test_detect_season_sorted_by_days_until(self, engine: SeasonalPricingEngine) -> None:
        """النتائج مرتبة تصاعدياً حسب days_until."""
        # عيد الحب: 7-16 فبراير، رمضان: 18 فبراير - 19 مارس
        # في 10 فبراير: عيد الحب نشط (0)، رمضان خلال 8 أيام
        d = date(2026, 2, 10)
        seasons = engine.detect_season(current_date=d)

        if len(seasons) >= 2:
            for i in range(len(seasons) - 1):
                assert seasons[i].days_until <= seasons[i + 1].days_until


# ════════════════════════════════════════════════════════════════════
#  اختبارات التنبيهات الموسمية
# ════════════════════════════════════════════════════════════════════

class TestSeasonalAlerts:
    """اختبارات get_seasonal_alerts()."""

    def test_alerts_return_arabic_text(self, engine: SeasonalPricingEngine) -> None:
        """التنبيهات تحتوي نصاً عربياً."""
        # خلال رمضان
        d = date(2026, 3, 1)
        alerts = engine.get_seasonal_alerts(current_date=d)

        assert len(alerts) > 0
        for alert in alerts:
            assert isinstance(alert, SeasonalAlert)
            # التحقق أن النص يحتوي أحرف عربية
            assert any("\u0600" <= c <= "\u06FF" for c in alert.body)
            assert any("\u0600" <= c <= "\u06FF" for c in alert.title)
            assert alert.season != ""

    def test_alerts_for_upcoming_season(self, engine: SeasonalPricingEngine) -> None:
        """تنبيهات المواسم القادمة تذكر عدد الأيام."""
        # قبل الجمعة البيضاء بـ 10 أيام
        d = date(2026, 11, 10)
        alerts = engine.get_seasonal_alerts(current_date=d)

        bf_alerts = [a for a in alerts if a.season == "الجمعة البيضاء"]
        assert len(bf_alerts) == 1
        assert "10" in bf_alerts[0].body
        assert bf_alerts[0].days_until == 10


# ════════════════════════════════════════════════════════════════════
#  اختبارات تعديل الأسعار
# ════════════════════════════════════════════════════════════════════

class TestAdjustPrice:
    """اختبارات adjust_price_for_season()."""

    def test_adjust_during_active_season(self, engine: SeasonalPricingEngine) -> None:
        """تعديل السعر خلال موسم نشط يؤثر على الفئة."""
        # خلال رمضان — العطور ترتفع 15%
        d = date(2026, 3, 1)
        adjusted, reason = engine.adjust_price_for_season(
            100.0, category="perfumes", current_date=d,
        )

        assert adjusted > 100.0  # ارتفاع
        assert adjusted == pytest.approx(115.0, abs=1.0)
        assert "رمضان" in reason
        assert reason != ""

    def test_no_adjustment_outside_season(self, engine: SeasonalPricingEngine) -> None:
        """لا تعديل خارج أي موسم."""
        d = date(2026, 10, 15)
        adjusted, reason = engine.adjust_price_for_season(
            100.0, category="perfumes", current_date=d,
        )

        assert adjusted == 100.0
        assert reason == ""

    def test_discount_during_black_friday(self, engine: SeasonalPricingEngine) -> None:
        """تخفيض خلال الجمعة البيضاء."""
        d = date(2026, 11, 25)
        adjusted, reason = engine.adjust_price_for_season(
            100.0, category="perfumes", current_date=d,
        )

        assert adjusted < 100.0  # تخفيض
        assert "الجمعة البيضاء" in reason


# ════════════════════════════════════════════════════════════════════
#  اختبارات المواسم القادمة
# ════════════════════════════════════════════════════════════════════

class TestUpcomingSeasons:
    """اختبارات get_upcoming_seasons()."""

    def test_respects_days_ahead(self, engine: SeasonalPricingEngine) -> None:
        """get_upcoming_seasons يحترم حدود days_ahead."""
        # قبل الجمعة البيضاء بـ 50 يوم (1 أكتوبر)
        d = date(2026, 10, 1)

        # 30 يوم — لن تظهر الجمعة البيضاء (50 يوم بعيدة)
        short = engine.get_upcoming_seasons(days_ahead=30, current_date=d)
        bf_short = [s for s in short if s.name == "الجمعة البيضاء"]
        assert len(bf_short) == 0

        # 60 يوم — ستظهر الجمعة البيضاء
        long = engine.get_upcoming_seasons(days_ahead=60, current_date=d)
        bf_long = [s for s in long if s.name == "الجمعة البيضاء"]
        assert len(bf_long) == 1


# ════════════════════════════════════════════════════════════════════
#  اختبارات إنشاء النماذج
# ════════════════════════════════════════════════════════════════════

class TestDataclassCreation:
    """اختبارات إنشاء SeasonInfo و SeasonalAlert."""

    def test_season_info_creation(self) -> None:
        """إنشاء SeasonInfo مع كل الحقول."""
        info = SeasonInfo(
            name="رمضان",
            active=True,
            days_until=0,
            expected_impact_pct=15.0,
            affected_categories=["perfumes", "cosmetics"],
            description="شهر رمضان المبارك",
        )

        assert info.name == "رمضان"
        assert info.active is True
        assert info.days_until == 0
        assert info.expected_impact_pct == 15.0
        assert len(info.affected_categories) == 2
        assert info.description == "شهر رمضان المبارك"

    def test_seasonal_alert_creation(self) -> None:
        """إنشاء SeasonalAlert مع كل الحقول."""
        alert = SeasonalAlert(
            season="رمضان",
            title="⚠️ رمضان خلال 15 يوم",
            body="⚠️ رمضان خلال 15 يوم — توقع ارتفاع أسعار العطور 15%",
            severity="warning",
            days_until=15,
            expected_impact_pct=15.0,
        )

        assert alert.season == "رمضان"
        assert alert.severity == "warning"
        assert alert.days_until == 15
        assert alert.alert_id != ""  # تم توليده تلقائياً
        assert isinstance(alert.created_at, datetime)

    def test_season_info_default_values(self) -> None:
        """القيم الافتراضية لـ SeasonInfo."""
        info = SeasonInfo()

        assert info.name == ""
        assert info.active is False
        assert info.days_until == 0
        assert info.expected_impact_pct == 0.0
        assert info.affected_categories == []
        assert info.description == ""


# ════════════════════════════════════════════════════════════════════
#  اختبارات المواسم المتزامنة
# ════════════════════════════════════════════════════════════════════

class TestConcurrentSeasons:
    """اختبارات المواسم المتزامنة."""

    def test_multiple_concurrent_seasons(self, engine: SeasonalPricingEngine) -> None:
        """اكتشاف مواسم متزامنة (نشطة + قادمة)."""
        # 10 فبراير: عيد الحب نشط (7-16 فبراير)، رمضان خلال 8 أيام (18 فبراير)
        d = date(2026, 2, 10)
        seasons = engine.detect_season(current_date=d)

        names = [s.name for s in seasons]
        # عيد الحب نشط
        assert "عيد الحب" in names
        valentine = [s for s in seasons if s.name == "عيد الحب"][0]
        assert valentine.active is True

        # رمضان قادم
        assert "رمضان" in names
        ramadan = [s for s in seasons if s.name == "رمضان"][0]
        assert ramadan.active is False
        assert ramadan.days_until == 8
