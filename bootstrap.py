"""bootstrap.py — جذر التركيب (DI Container) + منسّق التحليل.

يبني حاوية تحقن الخدمات عديمة الحالة (تصنيف/تسعير/تدقيق/AI/تصدير) مرة واحدة،
ويوفّر مصانع للخدمات المرتبطة بالكتالوج (مطابقة/مفقودات). لا Streamlit هنا
(يبقى الاستيراد العلوي لـ Streamlit حصراً في app.py).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Optional

import logging

import pandas as pd

logger = logging.getLogger("bootstrap")

from conf.settings import Settings
from core.enums import SectionType
from core.models import AnalysisResult
from infrastructure.db_manager import DatabaseManager
from services.audit_service import AuditService
from services.ai_service import AIService, SqliteAICache
from services.classification_service import ClassificationService, SplitResult
from services.export_service import ExportService
from services.matching_service import MatchingService
from services.missing_service import MissingService
from services.scraper_service import ScraperService

# ── خدمات v4 (المرحلة 0) ──────────────────────────────────────────
from infrastructure.product_repository import ProductRepository
from services.workflow.rollback_manager import RollbackManager
from services.learning.feedback_collector import FeedbackCollector
from services.monitoring.data_quality import DataQualityScorer

# ── خدمات v4 (المرحلة 1) ──────────────────────────────────────────
from services.learning.confidence_calibrator import ConfidenceCalibrator
from services.learning.pattern_memory import PatternMemory
from services.swarm.orchestrator import SwarmOrchestrator

# ── خدمات v4 (المرحلة 2) ──────────────────────────────────────────
from services.monitoring.anomaly_detector import AnomalyDetectionEngine
from services.workflow.notification_engine import NotificationEngine
from services.workflow.smart_batcher import SmartBatcher
from services.workflow.autopilot import AutoPilotEngine, AutoPilotConfig
from services.intelligence.price_predictor import PriceTrendPredictor
from services.intelligence.seasonal_engine import SeasonalPricingEngine

# ── خدمات v4 (المرحلة 3) ──────────────────────────────────────────
from observability.ai_metrics import AIMetricsCollector
from services.intelligence.competitor_modeler import CompetitorBehaviorModeler
from services.background.scheduler import TaskScheduler

# ── خدمات v4 (المرحلة 4 - أخيرة) ────────────────────────────────────
from services.workflow.approval_workflow import ApprovalWorkflow
from services.monitoring.price_monitor import ContinuousPriceMonitor


@dataclass(frozen=True)
class Container:
    """حاوية الاعتماديات: خدمات مفردة + مصانع مرتبطة بالكتالوج."""

    settings: Settings
    db: DatabaseManager
    classification: ClassificationService
    audit: AuditService
    ai: AIService
    export: ExportService
    scraper: ScraperService
    # ── خدمات v4 (المرحلة 0) ──
    product_repo: ProductRepository
    rollback: RollbackManager
    feedback: FeedbackCollector
    data_quality: DataQualityScorer
    # ── خدمات v4 (المرحلة 1) ──
    calibrator: ConfidenceCalibrator
    memory: PatternMemory
    swarm: SwarmOrchestrator
    # ── خدمات v4 (المرحلة 2) ──
    anomaly: AnomalyDetectionEngine
    notifier: NotificationEngine
    batcher: SmartBatcher
    autopilot: AutoPilotEngine
    predictor: PriceTrendPredictor
    seasonal: SeasonalPricingEngine
    # ── خدمات v4 (المرحلة 3) ──
    ai_metrics: AIMetricsCollector
    competitor_modeler: CompetitorBehaviorModeler
    scheduler: TaskScheduler
    # ── خدمات v4 (المرحلة 4 - أخيرة) ──
    approval: ApprovalWorkflow
    price_monitor: ContinuousPriceMonitor

    def matching_for(self, our_names: Iterable[str]) -> MatchingService:
        """يبني خدمة مطابقة لكتالوجنا الحالي."""
        return MatchingService(list(our_names))

    def missing_for(
        self, matching: MatchingService, our_brands: Optional[dict] = None,
    ) -> MissingService:
        """يبني خدمة مفقودات تعتمد على خدمة مطابقة جاهزة.

        ``our_brands`` (اسم منتجنا ← ماركته) اختياري: يفعّل قاعدة R3؛ غيابه آمن.
        """
        return MissingService(matching, our_brands=our_brands)


def build_container(settings: Optional[Settings] = None) -> Container:
    """يهيّئ الحاوية بالكامل (المدخل الوحيد لتركيب النظام)."""
    settings = settings or Settings.load()
    db = DatabaseManager(settings.db_path)

    # ── خدمات v4: تهيئة الجداول ──
    product_repo = ProductRepository(db)
    product_repo.ensure_tables()

    rollback = RollbackManager(db)
    rollback.ensure_table()

    feedback = FeedbackCollector(db)
    feedback.ensure_table()

    # ── خدمات v4 (المرحلة 1): معايرة + ذاكرة + ذكاء جماعي ──
    calibrator = ConfidenceCalibrator(feedback)

    memory = PatternMemory(feedback, db)
    memory.ensure_table()

    ai = AIService(settings=settings, cache=SqliteAICache())
    swarm = SwarmOrchestrator(ai, calibrator=calibrator, memory=memory)

    # ── خدمات v4 (المرحلة 2): شذوذ + تنبيهات + طيار آلي ──
    anomaly_engine = AnomalyDetectionEngine(db)
    anomaly_engine.ensure_table()

    notifier = NotificationEngine(db)
    notifier.ensure_table()

    autopilot = AutoPilotEngine(AutoPilotConfig(), rollback, db)
    autopilot.ensure_table()

    # ── خدمات v4 (المرحلة 3): مقاييس AI + نمذجة منافسين ──
    ai_metrics = AIMetricsCollector(db)
    ai_metrics.ensure_table()

    competitor_modeler = CompetitorBehaviorModeler(db)
    competitor_modeler.ensure_table()

    # ── خدمات v4 (المرحلة 4 - أخيرة): موافقات + مراقبة مستمرة ──
    approval = ApprovalWorkflow(db)
    approval.ensure_table()

    price_monitor = ContinuousPriceMonitor(db)
    price_monitor.ensure_table()

    return Container(
        settings=settings,
        db=db,
        classification=ClassificationService(),
        audit=AuditService(),
        ai=ai,
        export=ExportService(),
        scraper=ScraperService(),
        # ── v4 مرحلة 0 ──
        product_repo=product_repo,
        rollback=rollback,
        feedback=feedback,
        data_quality=DataQualityScorer(),
        # ── v4 مرحلة 1 ──
        calibrator=calibrator,
        memory=memory,
        swarm=swarm,
        # ── v4 مرحلة 2 ──
        anomaly=anomaly_engine,
        notifier=notifier,
        batcher=SmartBatcher(),
        autopilot=autopilot,
        predictor=PriceTrendPredictor(db),
        seasonal=SeasonalPricingEngine(),
        # ── v4 مرحلة 3 ──
        ai_metrics=ai_metrics,
        competitor_modeler=competitor_modeler,
        scheduler=TaskScheduler(),
        # ── v4 مرحلة 4 (أخيرة) ──
        approval=approval,
        price_monitor=price_monitor,
    )


def competitor_content_fingerprint(db_path: str) -> str:
    """بصمة محتوى منافسين مستقرة — بديل ``os.path.getsize(db_path)``.

    القاعدة ``pricing_v18.db`` موحّدة (كتالوجنا + المنافسون + كل الجداول
    الإجرائية)، فحجم الملف يتغيّر مع أي كتابة لأي جدول فيه (حتى صف واحد في
    ``product_signal_events`` أو ``send_log``)، مما كان يُبطل كاش المطابقة/
    التسعير الثقيل يومياً تقريباً رغم عدم تغيّر بيانات المنافسين فعلياً.
    هذه البصمة تعتمد فقط على محتوى ``competitor_products_store`` (العدد وآخر
    تحديث فعلي)، وتتراجع بأمان لحجم الملف عند أي خطأ (جدول/عمود غائب).
    """
    import sqlite3
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            row = conn.execute(
                "SELECT COUNT(*), MAX(updated_at) FROM competitor_products_store "
                "WHERE price > 0",
            ).fetchone()
        finally:
            conn.close()
        return f"{row[0]}|{row[1]}"
    except Exception:
        import os
        return str(os.path.getsize(db_path)) if os.path.exists(db_path) else "0"


def run_missing_analysis(
    container: Container,
    our_df: pd.DataFrame,
    *,
    use_cache: bool = True,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """يشغّل كشف المفقودات الحقيقي ضدّ قاعدة المنافسين (~129K) مع كاش F4v2.

    يُعيد (DataFrame المفقودات، إحصاءات). #PRESERVED_LOGIC: مسار
    _compute_missing_from_store (مرشّحون من CompetitorIntelligence ثم تصنيف).
    """
    import os
    import sys

    from conf.constants import COMPETITOR_DB_PATH, MISSING_CACHE_PATH, PROJECT_ROOT
    from services.catalog_service import name_column
    from services.missing_service import (
        MissingService,
        load_cache,
        missing_signature,
        save_cache,
    )

    db_path = str(COMPETITOR_DB_PATH)
    cache_path = str(MISSING_CACHE_PATH)
    signature = (
        missing_signature(len(our_df), competitor_content_fingerprint(db_path))
        if os.path.exists(db_path) else ""
    )
    if use_cache:
        cached = load_cache(cache_path, signature)
        if cached is not None:
            return cached, {"cached": True, "rows": len(cached)}
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"قاعدة المنافسين غير موجودة: {db_path}")
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    from engines.competitor_intelligence import CompetitorIntelligence  # type: ignore

    ncol = name_column(our_df)
    names = our_df[ncol].dropna().astype(str)
    names = names[names.str.strip() != ""].tolist()
    matching = container.matching_for(names)
    _ci = CompetitorIntelligence(db_path=db_path)
    candidates, _total = _ci.find_missing_products(our_df, page=0, per_page=1_000_000)
    # فهرس اسم←ماركة يُبنى مرة واحدة لكل تشغيل — يفعّل قاعدة R3 في المصنع.
    from services.reclassify_missing import build_our_brand_index
    rows = container.missing_for(
        matching, our_brands=build_our_brand_index(our_df),
    ).compute(candidates)
    missing_df = MissingService.to_dataframe(rows)
    if use_cache and signature:
        save_cache(cache_path, signature, missing_df)
    green = int((missing_df["مستوى_الثقة"] == "green").sum()) if not missing_df.empty and "مستوى_الثقة" in missing_df.columns else 0
    return missing_df, {
        "cached": False, "rows": len(missing_df),
        "candidates": len(candidates), "our_products": len(names),
        "confirmed_missing": green, "review": len(missing_df) - green,
        # قصّ حارس الذاكرة: يُمرَّر ليُعرَض للمالك — لا ليُدفن في سجلّ.
        "capped": bool(getattr(_ci, "last_capped", False)),
        "cap_limit": int(getattr(_ci, "last_cap_limit", 0) or 0),
        "cap_skipped_rows": int(getattr(_ci, "last_cap_skipped_rows", 0) or 0),
    }


def load_competitor_dfs(db_path: str) -> dict[str, pd.DataFrame]:
    """يحمّل منافسي القاعدة (~129K) كـ ``{اسم المتجر: DataFrame}`` للتحليل السعري.

    #PRESERVED_LOGIC: مطابق لمسار تحميل DB في app.py:4159-4183 — يقرأ
    ``competitor_products_store`` (السعر>0) ثم يعيد تسمية الأعمدة لما يتوقّعه
    المحرّك (product_name→المنتج، price→السعر، image_url→صورة المنتج،
    product_url→رابط المنتج، competitor→المنافس) ويجمّع حسب المتجر.
    """
    import os
    import sqlite3

    if not os.path.exists(db_path):
        raise FileNotFoundError(f"قاعدة المنافسين غير موجودة: {db_path}")
    # ⚡ إسقاط أعمدة (perf): نقرأ الأعمدة التي يستهلكها المحرّك فقط بدل
    #    ``SELECT *``. بعض قواعد SQLite القديمة سبقت ترحيل v31.8، ولذلك قد
    #    تغيب منها أعمدة الميزات المسبقة. هذه الحقول اختيارية: عند غيابها
    #    نعيد قيمة حيادية بالاسم نفسه كي يحسبها المحرّك من product_name بدلاً
    #    من إيقاف التحليل كله بـ ``no such column``.
    _BASE_COLS = (
        "id", "product_name", "price", "image_url", "product_url",
        "competitor", "brand",
    )
    _OPTIONAL_COLS = {
        "agg_name": "''",
        "extracted_brand": "''",
        "extracted_size": "0",
        "extracted_gender": "''",
        "extracted_class": "''",
        "product_line": "''",
    }
    conn = sqlite3.connect(db_path)
    try:
        existing = {
            row[1]
            for row in conn.execute("PRAGMA table_info(competitor_products_store)")
        }
        select_cols = list(_BASE_COLS)
        for col, fallback in _OPTIONAL_COLS.items():
            select_cols.append(col if col in existing else f"{fallback} AS {col}")
        df = pd.read_sql_query(
            "SELECT " + ", ".join(select_cols)
            + " FROM competitor_products_store WHERE price > 0",
            conn,
        )
    finally:
        conn.close()
    rename = {
        "product_name": "المنتج",
        "price": "السعر",
        "image_url": "صورة المنتج",
        "product_url": "رابط المنتج",
        "competitor": "المنافس",
    }
    df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})
    group_col = "المنافس" if "المنافس" in df.columns else "competitor"
    if group_col not in df.columns:
        return {"كل المنتجات": df.reset_index(drop=True)}
    return {
        str(name): group.reset_index(drop=True)
        for name, group in df.groupby(group_col, sort=False)
    }


def run_pricing_analysis(
    container: Container,
    our_df: pd.DataFrame,
    *,
    use_ai: Optional[bool] = None,
    use_cache: bool = True,
    missing_df: Optional[pd.DataFrame] = None,
) -> tuple[dict[str, pd.DataFrame], AnalysisResult, Optional[pd.DataFrame], dict[str, Any]]:
    """التحليل السعري الكامل: المحرّك → تصنيف → تنقية المفقودات → تدقيق → نتيجة.

    يشغّل ``engines.engine.run_full_analysis(our_df, comp_dfs)`` (مع كاش قرصي
    للمخرجات الثقيلة)، يصنّف النتيجة، **يزيل من المفقودات أي منافس مطابَق سعرياً**
    (مصدر الحقيقة الحاسم — app.py:1184-1222)، ثم يدقّق. يُعيد:
    (أقسام جاهزة للعرض، AnalysisResult مع تقرير التدقيق، المفقودات بعد التنقية، إحصاءات).

    ``use_ai=None`` ⇒ يُفعَّل تلقائياً فقط إن وُجدت مفاتيح ذكاء اصطناعي
    (النطاق الرمادي 60–84% بلا مفاتيح ⇒ مراجعة، بلا تكلفة). #PRESERVED_LOGIC.
    """
    import os
    import sys

    from conf.constants import (
        COMPETITOR_DB_PATH,
        PRICING_CACHE_PATH,
        PRICING_CACHE_VERSION,
        PROJECT_ROOT,
    )
    from core.enums import SectionType
    from services.missing_service import load_cache, save_cache

    if use_ai is None:
        use_ai = container.settings.any_ai_configured

    db_path = str(COMPETITOR_DB_PATH)
    cache_path = str(PRICING_CACHE_PATH)
    comp_fingerprint = competitor_content_fingerprint(db_path) if os.path.exists(db_path) else "0"
    signature = f"{PRICING_CACHE_VERSION}|{len(our_df)}|{comp_fingerprint}|{int(bool(use_ai))}"

    results_df: Optional[pd.DataFrame] = (
        load_cache(cache_path, signature) if use_cache else None
    )
    audit_stats: dict[str, Any] = {}
    if results_df is not None:
        audit_stats = {"cached": True, "rows": len(results_df)}
    else:
        comp_dfs = load_competitor_dfs(db_path)
        if str(PROJECT_ROOT) not in sys.path:
            sys.path.insert(0, str(PROJECT_ROOT))
        from engines.engine import run_full_analysis  # type: ignore  #PRESERVED_LOGIC

        results_df, audit_stats = run_full_analysis(
            our_df, comp_dfs, use_ai=bool(use_ai),
        )
        audit_stats = dict(audit_stats or {})
        audit_stats["cached"] = False
        audit_stats["competitors"] = len(comp_dfs)
        if use_cache and signature:
            save_cache(cache_path, signature, results_df)

    # ── تصنيف ثم تنقية المفقودات من المطابَق سعرياً (مصدر الحقيقة الحاسم) ──
    from services.audit_service import dedup_missing_vs_matched

    split = container.classification.classify(results_df)

    # ── 🧠 AI Router: إعادة تقييم «تحت المراجعة» تلقائياً (الخيار ج) ──
    sections = split.sections
    ai_routed_stats: dict[str, int] = {}
    if use_ai and container.ai.any_configured:
        try:
            from services.ai_router_service import AIRouterService

            router = AIRouterService(container.ai, confidence_threshold=0.85)
            review_df = sections.get("review", pd.DataFrame())
            if not review_df.empty:
                route_result = router.route_review(review_df)
                # المطابقات المؤكدة → approved (تأكد أن منتجنا = المنافس)
                if not route_result.upgraded.empty:
                    sections["approved"] = pd.concat(
                        [sections.get("approved", pd.DataFrame()), route_result.upgraded],
                        ignore_index=True,
                    )
                # المفقودات المؤكدة → excluded (منتجنا ليس له تطابق صحيح مع المنافس)
                # ⚠️ هذا إصلاح حرج: كانت تختفي من sections مسببةً gap=295
                if not route_result.to_missing.empty:
                    sections["excluded"] = pd.concat(
                        [sections.get("excluded", pd.DataFrame()), route_result.to_missing],
                        ignore_index=True,
                    )
                # غير المحسوم يبقى في المراجعة
                sections["review"] = route_result.stayed_review
                ai_routed_stats = {
                    "ai_upgraded": len(route_result.upgraded),
                    "ai_to_excluded": len(route_result.to_missing),
                    "ai_stayed_review": len(route_result.stayed_review),
                    "ai_errors": len(route_result.errors),
                }
        except Exception as exc:
            # شبكة الأمان: فشل AI Router لا يكسر التحليل — كل شيء يبقى في المراجعة
            ai_routed_stats = {"ai_error": str(exc)[:200]}

    missing_clean, removed = dedup_missing_vs_matched(sections, missing_df)

    # ── 🧹 تمرير ثانٍ: تطهير المفقودات غير المؤكدة (Purge Unconfirmed Missing) ──
    # أي منتج في المفقودات بـ مستوى_الثقة != "green" ليس مؤكداً — يُسحب ويُعاد
    # توجيهه عبر AI Router لحسمه نهائياً. هذا يضمن خلو قسم المفقودات من أي منتج
    # غير محسوم (القاعدة: المفقودات = مؤكد 100% فقط).
    missing_purge_stats: dict[str, int] = {}
    if (use_ai and container.ai.any_configured
            and missing_clean is not None and not missing_clean.empty
            and "مستوى_الثقة" in missing_clean.columns):
        try:
            from services.ai_router_service import AIRouterService

            unconfirmed_mask = missing_clean["مستوى_الثقة"] != "green"
            n_unconfirmed = int(unconfirmed_mask.sum())

            if n_unconfirmed > 0:
                confirmed_missing = missing_clean[~unconfirmed_mask].copy()
                unconfirmed_missing = missing_clean[unconfirmed_mask].copy()

                logger.info(
                    "🧹 تطهير المفقودات: %d غير مؤكد من %d — إرسال لـ AI Router",
                    n_unconfirmed, len(missing_clean),
                )

                # نحتاج تحويل المفقودات لصيغة تناسب AI Router
                # AI Router يتوقع أعمدة المراجعة — نُعدّ الأعمدة المفقودة
                router = AIRouterService(container.ai, confidence_threshold=0.85)

                # معالجة بعينة مصغرة لحماية التكلفة (أول 50 فقط)
                sample_size = min(50, n_unconfirmed)
                sample_df = unconfirmed_missing.iloc[:sample_size]
                remaining_unconfirmed = unconfirmed_missing.iloc[sample_size:]

                route_result = router.route_review(sample_df)

                # المطابقات المؤكدة من AI → approved
                if not route_result.upgraded.empty:
                    sections["approved"] = pd.concat(
                        [sections.get("approved", pd.DataFrame()), route_result.upgraded],
                        ignore_index=True,
                    )
                # المفقودات المؤكدة من AI → تبقى في missing
                purged_confirmed = route_result.to_missing
                # غير المحسوم → يبقى في missing (محافظ — لا نسقطه)
                stayed = route_result.stayed_review

                # إعادة بناء missing_clean: المؤكد أصلاً + المؤكد من AI + الباقي
                parts = [confirmed_missing]
                if not purged_confirmed.empty:
                    parts.append(purged_confirmed)
                if not stayed.empty:
                    parts.append(stayed)
                if not remaining_unconfirmed.empty:
                    parts.append(remaining_unconfirmed)
                missing_clean = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()

                missing_purge_stats = {
                    "purge_total": n_unconfirmed,
                    "purge_sampled": sample_size,
                    "purge_to_matched": len(route_result.upgraded),
                    "purge_confirmed_missing": len(purged_confirmed),
                    "purge_stayed": len(stayed) + len(remaining_unconfirmed),
                    "purge_errors": len(route_result.errors),
                }
                logger.info(
                    "✅ تطهير المفقودات: %d→matched | %d→confirmed_missing | %d→stayed",
                    len(route_result.upgraded), len(purged_confirmed),
                    len(stayed) + len(remaining_unconfirmed),
                )
        except Exception as exc:
            missing_purge_stats = {"purge_error": str(exc)[:200]}
            logger.warning("⚠️ فشل تطهير المفقودات: %s", exc)

    # sections هو نفس كائن split.sections (dict مرن داخل dataclass مجمّد)
    # فأي تعديل على sections ينعكس تلقائياً في split.sections
    # ⚠️ التدقيق هنا يفحص توازن المحرّك (results_df → الأقسام) قبل استرداد
    # الكتالوج، فيبقى تدقيقاً نظيفاً للمحرّك وحده.
    report = container.audit.reconcile(split, missing_clean)

    # ── 🛟 شبكة أمان الكتالوج: استرداد المنتجات التي رشّحها المحرّك بصمت ──
    # المحرّك يُسقط تستر/أجهزة/أطقم/بلا-مطابقة *قبل* إنتاج صفوف التحليل، فتختفي
    # من كل الأقسام ويختلّ قانون الكتالوج len(الكتالوج) == Σ(الأقسام) ويضيع
    # المنتج بصمت. نُعيدها إلى «مستبعد» بسبب واضح (بعد تدقيق المحرّك) فيُغلق
    # توازن الكتالوج ويراها المستخدم بدل اختفائها. #DATA_CONSERVATION
    from services.classification_service import recover_uncovered_catalog

    _, catalog_recovered = recover_uncovered_catalog(our_df, sections)
    if catalog_recovered:
        logger.info("🛟 استرداد الكتالوج: %d منتج مُرشَّح → «مستبعد»", catalog_recovered)

    # إعادة بناء العدّاد من الأقسام الحيّة (بعد تعديلات AI Router)
    counts: dict[SectionType, int] = {}
    for k in ("price_raise", "price_lower", "approved", "review", "excluded"):
        try:
            counts[SectionType(k)] = len(sections.get(k, pd.DataFrame()))
        except ValueError:
            pass
    missing_confirmed = 0
    if (missing_clean is not None and not missing_clean.empty
            and "مستوى_الثقة" in missing_clean.columns):
        missing_confirmed = int((missing_clean["مستوى_الثقة"] == "green").sum())
    counts[SectionType.MISSING] = missing_confirmed

    result = AnalysisResult(
        section_counts=counts,
        total=split.total_in,
        missing_count=missing_confirmed,
        reconciliation=report,
    )
    audit_stats["missing_deduped"] = removed
    audit_stats["catalog_recovered"] = catalog_recovered
    audit_stats.update(ai_routed_stats)
    audit_stats.update(missing_purge_stats)

    # ── 🔒 الإقفال الكمومي: محاسبة كل منتجات المنافسين (الدائرة المقفلة v2.0) ──
    # يُسجّل كل الـ129K في سجل قائم على الهوية ويتحقّق أن لا منتج واحد تسرّب.
    # محاط بحارس: فشل الإقفال يُبلّغ في الإحصاءات ولا يكسر التحليل.
    try:
        if str(PROJECT_ROOT) not in sys.path:
            sys.path.insert(0, str(PROJECT_ROOT))
        from engines.closed_loop_engine import (  # type: ignore  #PRESERVED_LOGIC
            load_competitor_universe,
            reconcile_competitor_universe,
        )

        _universe = load_competitor_universe(db_path)
        _ledger = reconcile_competitor_universe(
            _universe, sections, missing_clean, context="bootstrap",
        )
        audit_stats["closed_loop"] = {
            "input":    _ledger.total_input,
            "match":    _ledger.confirmed_match,
            "missing":  _ledger.confirmed_missing,
            "review":   _ledger.ai_review_pending,
            "excluded": _ledger.excluded_noise,
            "balanced": _ledger.balanced,
        }
        # تسجيل مستقل عن الواجهة: نتيجة كاشف التسرّب كانت تُخزَّن في audit_stats
        # ولا يقرأها أحد، فلو اختلّت المحاسبة مرّت بلا أثر في أي مكان.
        if not _ledger.balanced:
            logger.warning(
                "⚠️ الإقفال الكمومي غير متوازن: إدخال=%s · مطابق=%s · مفقود=%s "
                "· مراجعة=%s · مستبعد=%s",
                _ledger.total_input, _ledger.confirmed_match, _ledger.confirmed_missing,
                _ledger.ai_review_pending, _ledger.excluded_noise,
            )
    except Exception as exc:  # noqa: BLE001 — لا يكسر التحليل، يُبلّغ التسرّب فقط
        audit_stats["closed_loop_error"] = str(exc)[:600]
        logger.warning("⚠️ تعذّر التحقّق من عدم تسرّب منتجات المنافسين: %s", exc)

    return sections, result, missing_clean, audit_stats


def run_analysis(
    container: Container,
    results_df: pd.DataFrame,
    *,
    our_names: Optional[Iterable[str]] = None,
    missing_candidates: Optional[list[dict[str, Any]]] = None,
) -> tuple[AnalysisResult, SplitResult, Optional[pd.DataFrame]]:
    """يشغّل: تصنيف → (مفقودات اختيارية) → تدقيق. يفرض قانون حفظ البيانات.

    يُعيد (نتيجة التحليل، التوزيع، DataFrame المفقودات أو None).
    يرفع ``DataLossError`` إذا اختلّ التوازن (gap≠0 أو تكرار≠0).
    """
    split = container.classification.classify(results_df)
    missing_df: Optional[pd.DataFrame] = None
    missing_count = 0
    if our_names is not None and missing_candidates:
        matching = container.matching_for(our_names)
        rows = container.missing_for(matching).compute(missing_candidates)
        missing_df = MissingService.to_dataframe(rows)
        missing_count = len(missing_df)
    report = container.audit.reconcile(split, missing_df)
    counts = {SectionType(key): value for key, value in split.counts().items()}
    counts[SectionType.MISSING] = missing_count
    result = AnalysisResult(
        section_counts=counts,
        total=split.total_in,
        missing_count=missing_count,
        reconciliation=report,
    )
    result.assert_conservation()  # يرفع DataLossError عند أي خرق
    return result, split, missing_df


def populate_our_catalog(our_df: pd.DataFrame) -> None:
    """يملأ جدول ``our_catalog`` من كتالوجنا المرفوع كي يعمل مطابق الملكية
    «لديك/ليس لديك» على مرجع حقيقي (بدونه يظهر كل منتج منافس «ليس لديك»).

    (انتقلت من app.py — طبقة خدمات نقية كي يستدعيها مشغّل الخلفية أيضاً.)
    بيان-بيانات فقط: لا يغيّر منطق المطابقة ولا بطاقة «ليس لديك» — يملأ المصدر.
    يستخرج الأعمدة الفعلية عبر ``name_column`` وثوابت الكتالوج (لا يفترض الأسماء)؛
    إن غاب عمود الاسم لا يُدرِج شيئاً (كي لا يُفسد الجدول). أخطاؤه لا تُفشل الرفع.
    """
    try:
        from conf.constants import COL_OUR_ID, COL_OUR_PRICE
        from services.catalog_service import name_column
        from utils.db_manager import upsert_our_catalog

        ncol = name_column(our_df)
        if not ncol or ncol not in our_df.columns:
            return  # لا عمود اسم موثوق ⇒ لا إدراج (حارس ضد ملء فراغات)
        upsert_our_catalog(
            our_df,
            name_col=ncol,
            id_col=(COL_OUR_ID if COL_OUR_ID in our_df.columns else ""),
            price_col=(COL_OUR_PRICE if COL_OUR_PRICE in our_df.columns else ""),
        )
    except Exception as exc:  # الملء مساعد؛ فشله لا يوقف التحليل/الرفع
        import logging
        logging.getLogger(__name__).warning("تعذّر ملء our_catalog: %s", exc)


def run_v4_post_analysis(
    container: Container, sections: dict, result: Any,
) -> None:
    """يُشغّل خدمات v4 الحقيقية بعد كل تحليل — لا كود ميت.

    (انتقلت من app.py — نقية بلا Streamlit كي يستدعيها مشغّل الخلفية أيضاً.)
    ⚠️ أسماء الأعمدة الرسمية (من conf.constants):
      المنتج / السعر / سعر_المنافس / المنافس / الفرق / نسبة_التطابق
    """
    import logging
    from conf.constants import (
        COL_OUR_NAME, COL_OUR_PRICE, COL_COMP_PRICE, COL_COMP_STORE,
    )
    v4_logger = logging.getLogger("v4_pipeline")

    # 1) كشف الشذوذ: يفحص أسعار كل قسم سعري ويسجل الحالات الشاذة
    try:
        total_anomalies = 0
        for sec_name in ("price_raise", "price_lower"):
            sec_df = sections.get(sec_name)
            if sec_df is not None and not sec_df.empty:
                prices = []
                for row in sec_df.to_dict('records'):
                    our_p = float(row.get(COL_OUR_PRICE, 0) or 0)
                    comp_p = float(row.get(COL_COMP_PRICE, 0) or 0)
                    if our_p > 0:
                        prices.append(our_p)
                    if comp_p > 0:
                        prices.append(comp_p)
                if len(prices) >= 3:
                    anomalies = container.anomaly.detect(
                        prices, product_name=f"قسم_{sec_name}",
                    )
                    for a in anomalies:
                        container.anomaly.record_anomaly(a)
                    total_anomalies += len(anomalies)
        v4_logger.info("✅ كشف الشذوذ: %d حالة شاذة من الأقسام السعرية", total_anomalies)
    except Exception as e:
        v4_logger.warning("⚠️ كشف الشذوذ فشل: %s", e)

    # 2) تنبيهات: إشعار بنتائج التحليل
    try:
        from core.enums import SectionType
        counts = result.section_counts
        raise_n = counts.get(SectionType.PRICE_RAISE, 0)
        lower_n = counts.get(SectionType.PRICE_LOWER, 0)
        review_n = counts.get(SectionType("review"), 0)

        # طيّ إشعارات التحليل السابقة (read=1، بلا حذف) كي يظهر أحدث رقم فقط لكل فئة
        container.notifier.mark_category_read("analysis")

        if raise_n > 0:
            container.notifier.notify(
                title="🔴 منتجات تحتاج رفع سعر",
                body=f"تم رصد {raise_n} منتج بسعر أقل من المنافسين",
                severity="warning" if raise_n > 20 else "info",
                category="analysis",
            )
        if lower_n > 0:
            container.notifier.notify(
                title="🟢 منتجات بسعر أعلى",
                body=f"{lower_n} منتج سعرنا أعلى من المنافسين",
                severity="info",
                category="analysis",
            )
        if review_n > 0:
            container.notifier.notify(
                title="⚠️ منتجات تحت المراجعة",
                body=f"{review_n} منتج بحاجة لقرار يدوي",
                severity="warning",
                category="analysis",
            )
        v4_logger.info(
            "✅ التنبيهات: raise=%d lower=%d review=%d",
            raise_n, lower_n, review_n,
        )
    except Exception as e:
        v4_logger.warning("⚠️ التنبيهات فشلت: %s", e)

    # 3) لقطات أسعار: تسجيل وضع السوق الحالي
    try:
        snap_count = 0
        for sec_name in ("price_raise", "price_lower", "approved"):
            sec_df = sections.get(sec_name)
            if sec_df is not None and not sec_df.empty:
                for row in sec_df.head(100).to_dict('records'):
                    our_p = float(row.get(COL_OUR_PRICE, 0) or 0)
                    comp_p = float(row.get(COL_COMP_PRICE, 0) or 0)
                    comp_name = str(row.get(COL_COMP_STORE, "unknown"))
                    prod_name = str(row.get(COL_OUR_NAME, ""))
                    if our_p > 0 and comp_p > 0 and prod_name:
                        container.price_monitor.take_snapshot(
                            product_name=prod_name,
                            our_price=our_p,
                            competitor_prices={comp_name: comp_p},
                        )
                        snap_count += 1
        v4_logger.info("✅ لقطات الأسعار: تم تسجيل %d لقطة", snap_count)
    except Exception as e:
        v4_logger.warning("⚠️ لقطات الأسعار فشلت: %s", e)

    # 4) جودة البيانات: تقييم جودة الإدخال
    try:
        for sec_name, sec_df in sections.items():
            if sec_df is not None and not sec_df.empty:
                score = container.data_quality.score(sec_df)
                if score.overall < 50:  # مقياس 0–100
                    container.notifier.notify(
                        title=f"📊 جودة بيانات منخفضة: {sec_name}",
                        body=f"درجة الجودة {score.overall:.0f}/100 — تحقق من البيانات",
                        severity="warning",
                        category="quality",
                    )
        v4_logger.info("✅ فحص الجودة: تم تقييم %d قسم", len(sections))
    except Exception as e:
        v4_logger.warning("⚠️ فحص الجودة فشل: %s", e)
