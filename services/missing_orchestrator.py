"""services/missing_orchestrator.py — محرك تجميع المفقودات الآلي (The Assembly Line).

══════════════════════════════════════════════════════════════════════════
MissingProductsOrchestrator — يربط جميع خطوات معالجة المفقودات في خط
تجميع واحد: فلترة الثقة → إثراء Fragrantica → وصف مهووس ذهبي →
مطابقة ماركة + تصنيف → بوابة جودة → تصدير (سلة CSV أو Make.com).

المصادر:
  - الطابور المُدار: ``utils.missing_queue_manager.get_ready_products()``
  - أو DataFrame مباشر من ``session_state`` (صفحة المفقودات)

القاعدة الذهبية: لا يُصدَّر منتج إلا إذا:
  1. مستوى_الثقة == "green"
  2. وصف بتنسيق مهووس (علامات: الهرم العطري / مهووس / لماذا تختار …)
  3. صورة حقيقية (http/https، ليست placeholder)
  4. ماركة غير فارغة
  5. سعر > 0
══════════════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Literal

import pandas as pd

logger = logging.getLogger("MissingOrchestrator")


# ══════════════════════════════════════════════════════════════════════════
#  نموذج تقرير التنفيذ
# ══════════════════════════════════════════════════════════════════════════
@dataclass
class OrchestratorReport:
    """تقرير شامل عن تنفيذ خط التجميع."""

    started_at: str = ""
    finished_at: str = ""
    total_input: int = 0
    green_count: int = 0
    skipped_not_green: int = 0
    enriched_ok: int = 0
    enriched_failed: int = 0
    gate_passed: int = 0
    gate_rejected: int = 0
    gate_reasons: Dict[str, int] = field(default_factory=dict)
    exported_count: int = 0
    export_target: str = ""
    export_result: Dict[str, Any] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    skipped_details: List[Dict[str, str]] = field(default_factory=list)

    def summary_text(self) -> str:
        """ملخص نصي مختصر للتقرير."""
        lines = [
            f"══ تقرير المحرك التنفيذي ══",
            f"⏱️  بدأ: {self.started_at}  |  انتهى: {self.finished_at}",
            f"📥 الإدخال الكلي: {self.total_input}",
            f"🟢 مؤكد (green): {self.green_count}  |  ⛔ مستبعد (ليس green): {self.skipped_not_green}",
            f"🔬 إثراء ناجح: {self.enriched_ok}  |  ❌ فشل الإثراء: {self.enriched_failed}",
            f"✅ عبر البوابة: {self.gate_passed}  |  🚫 مرفوض: {self.gate_rejected}",
            f"📤 تم التصدير: {self.exported_count}  |  الوجهة: {self.export_target}",
        ]
        if self.gate_rejected and self.gate_reasons:
            lines.append(_format_gate_reasons(self.gate_reasons))
        if self.errors:
            lines.append(f"⚠️  أخطاء: {len(self.errors)}")
        return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════
#  المحرك التنفيذي
# ══════════════════════════════════════════════════════════════════════════
class MissingProductsOrchestrator:
    """محرك أوتمتة شامل لمعالجة المنتجات المفقودة.

    يقرأ من الطابور أو من DataFrame مباشر، يفلتر المؤكدات (green) فقط،
    يُثريها بالمكونات العطرية والوصف الذهبي، يمررها عبر بوابة الجودة،
    ثم يُصدّرها إلى سلة CSV أو يُرسلها عبر Make.com.

    Usage::

        orch = MissingProductsOrchestrator()
        report = orch.process_queue(export_target="salla")
        print(report.summary_text())

        # أو من DataFrame:
        report = orch.process_dataframe(missing_df, export_target="make")
    """

    # ── مُنشئ ────────────────────────────────────────────────────────────
    def __init__(
        self,
        our_catalog_df: Optional[pd.DataFrame] = None,
        enrich_with_ai: bool = True,
        batch_size: int = 20,
        inter_delay: float = 0.3,
        skip_dedup: bool = False,
    ) -> None:
        """
        Args:
            our_catalog_df: كتالوجنا للتحقق من عدم التكرار (اختياري).
            enrich_with_ai: تفعيل الإثراء بالذكاء الاصطناعي (Fragrantica + وصف).
            batch_size: حجم الدفعة عند إرسال Make.com.
            inter_delay: تأخير (ثوانٍ) بين دفعات Make.com.
            skip_dedup: تخطّي بوابة منع التكرار (إرسال متعمّد «رغم التشابه» بقرار
                المستخدم — لمنتج احتجزته البوابة خطأً، كنسخة تركيز مختلفة).
        """
        self._catalog = our_catalog_df
        self._enrich_ai = enrich_with_ai
        self._batch_size = batch_size
        self._delay = inter_delay
        self._skip_dedup = skip_dedup

    # ══════════════════════════════════════════════════════════════════════
    #  نقطة الدخول 1: من الطابور (missing_queue_manager)
    # ══════════════════════════════════════════════════════════════════════
    def process_queue(
        self,
        export_target: Literal["salla", "make"] = "salla",
        output_path: str = "",
    ) -> OrchestratorReport:
        """معالجة المنتجات الجاهزة في الطابور وتصديرها.

        يسحب المنتجات ذات الحالة ``ready_to_send`` من
        ``utils.missing_queue_manager`` ويمررها عبر خط التجميع.

        Args:
            export_target: ``"salla"`` لتصدير CSV أو ``"make"`` لإرسال webhook.
            output_path: مسار ملف التصدير (لسلة فقط).

        Returns:
            ``OrchestratorReport`` بتفاصيل التنفيذ.
        """
        from utils.missing_queue_manager import get_ready_products

        products = get_ready_products()
        logger.info("📋 الطابور: %d منتج جاهز (ready_to_send)", len(products))

        return self._run_pipeline(products, export_target, output_path, source="queue")

    # ══════════════════════════════════════════════════════════════════════
    #  نقطة الدخول 2: من DataFrame مباشر (صفحة المفقودات / session_state)
    # ══════════════════════════════════════════════════════════════════════
    def process_dataframe(
        self,
        missing_df: pd.DataFrame,
        export_target: Literal["salla", "make"] = "salla",
        output_path: str = "",
    ) -> OrchestratorReport:
        """معالجة DataFrame مفقودات وتصديره.

        يفلتر المنتجات المؤكدة (``مستوى_الثقة == "green"``) فقط،
        يُثريها، ثم يُصدّرها.

        Args:
            missing_df: DataFrame المفقودات (من التحليل أو session_state).
            export_target: ``"salla"`` أو ``"make"``.
            output_path: مسار ملف التصدير (لسلة فقط).

        Returns:
            ``OrchestratorReport`` بتفاصيل التنفيذ.
        """
        if missing_df is None or missing_df.empty:
            report = OrchestratorReport(
                started_at=_now(), finished_at=_now(),
                export_target=export_target,
            )
            report.errors.append("DataFrame فارغ — لا منتجات للمعالجة")
            return report

        products = missing_df.to_dict("records")
        return self._run_pipeline(products, export_target, output_path, source="dataframe")

    # ══════════════════════════════════════════════════════════════════════
    #  خط التجميع المركزي (Private Pipeline)
    # ══════════════════════════════════════════════════════════════════════
    def _run_pipeline(
        self,
        products: List[Dict],
        export_target: str,
        output_path: str,
        source: str,
    ) -> OrchestratorReport:
        """خط التجميع الكامل: فلترة → إثراء → بوابة → تصدير."""

        report = OrchestratorReport(
            started_at=_now(),
            total_input=len(products),
            export_target=export_target,
        )

        if not products:
            report.finished_at = _now()
            report.errors.append("لا توجد منتجات للمعالجة")
            logger.warning("⚠️ خط التجميع: لا منتجات — خروج مبكر")
            return report

        # ── المرحلة 1: فلترة الثقة (green فقط) ────────────────────────
        green_products, skipped = self._filter_green(products)
        report.green_count = len(green_products)
        report.skipped_not_green = len(skipped)
        report.skipped_details = skipped
        logger.info(
            "🔍 فلترة الثقة: %d مؤكد (green) | %d مستبعد",
            len(green_products), len(skipped),
        )

        if not green_products:
            report.finished_at = _now()
            report.errors.append("لا توجد منتجات مؤكدة (green) — لا شيء للتصدير")
            return report

        # ── المرحلة 2: الإثراء (Fragrantica + وصف + ماركة + تصنيف) ────
        enriched = []
        for product in green_products:
            try:
                enriched_row = self._enrich_and_format(product)
                enriched.append(enriched_row)
                report.enriched_ok += 1
            except Exception as exc:
                report.enriched_failed += 1
                pname = _get_name(product)
                report.errors.append(f"فشل إثراء «{pname[:50]}»: {exc}")
                logger.error("❌ فشل إثراء «%s»: %s", pname[:50], exc)
                # مع ذلك، نضيف المنتج بالبيانات الأصلية (fallback)
                enriched.append(product)

        # ── المرحلة 3: بوابة الجودة الذهبية ───────────────────────────
        gate_passed, gate_rejected = self._apply_quality_gate(enriched)
        report.gate_passed = len(gate_passed)
        report.gate_rejected = len(gate_rejected)
        report.gate_reasons = _gate_reason_counts(gate_rejected)
        logger.info(
            "✅ بوابة الجودة: %d عبر | %d مرفوض",
            len(gate_passed), len(gate_rejected),
        )
        if gate_rejected:
            logger.info(_format_gate_reasons(report.gate_reasons))
            _traces = _format_gate_traces(gate_rejected)
            if _traces:
                logger.info("تفصيل أسباب الإثراء:\n%s", _traces)

        if not gate_passed:
            report.finished_at = _now()
            report.errors.append(_format_gate_reasons(report.gate_reasons))
            return report

        # ── المرحلة 4: التصدير ────────────────────────────────────────
        export_result = self._execute_export(
            gate_passed, export_target, output_path, source,
        )
        report.export_result = export_result
        report.exported_count = export_result.get("count", export_result.get("sent", 0))

        report.finished_at = _now()
        logger.info("📤 %s", report.summary_text())
        return report

    # ══════════════════════════════════════════════════════════════════════
    #  المرحلة 1: فلترة الثقة
    # ══════════════════════════════════════════════════════════════════════
    @staticmethod
    def _filter_green(
        products: List[Dict],
    ) -> tuple[List[Dict], List[Dict[str, str]]]:
        """يُمرّر المنتجات المؤكدة (green) فقط ويرفض الباقي.

        يبحث في مفاتيح: ``مستوى_الثقة``, ``confidence_level``, ``confidence``.
        القيم المقبولة: ``"green"``, ``"confirmed"``, ``"sure_missing"``.

        Returns:
            (green_list, skipped_details_list)
        """
        _GREEN_VALUES = frozenset({"green", "confirmed", "sure_missing"})

        green, skipped = [], []
        for p in products:
            conf = str(
                p.get("مستوى_الثقة")
                or p.get("confidence_level")
                or p.get("confidence")
                or ""
            ).strip().lower()

            if conf in _GREEN_VALUES:
                green.append(p)
            else:
                skipped.append({
                    "name": _get_name(p),
                    "confidence": conf or "غير محدد",
                    "reason": f"مستوى الثقة «{conf or 'فارغ'}» ≠ green",
                })
        return green, skipped

    # ══════════════════════════════════════════════════════════════════════
    #  المرحلة 2: الإثراء والتنسيق
    # ══════════════════════════════════════════════════════════════════════
    def _enrich_and_format(self, product: Dict) -> Dict:
        """يُثري منتجاً واحداً بالمكونات العطرية والوصف الذهبي والماركة والتصنيف.

        الخطوات:
          1. جلب بيانات Fragrantica (إن مفعّل).
          2. حل الماركة عبر BrandManager.
          3. تحديد التصنيف.
          4. توليد/تحسين الوصف الذهبي.
          5. ضمان وجود SKU.

        Args:
            product: قاموس بيانات المنتج الخام.

        Returns:
            قاموس محدّث ببيانات الإثراء.
        """
        enriched = dict(product)  # نسخة عمل
        pname = _get_name(enriched)
        if not pname:
            return enriched

        # ── 1. إثراء Fragrantica (نوتات + عائلة عطرية) ────────────────
        frag_data = {}
        if self._enrich_ai:
            frag_data = self._fetch_fragrantica_safe(pname)
            if frag_data.get("success"):
                _merge_frag(enriched, frag_data)
                # علم التأريض: نوتات من بحث مؤرَّض (موثوق) أو احتياطي AI (ثقة
                # منخفضة) ⇒ يقرؤه حارس البوابة assess_notes_quality للمراجعة.
                enriched["_notes_grounded"] = bool(frag_data.get("_grounded", False))
                logger.debug("🌸 إثراء Fragrantica ناجح: «%s»", pname[:40])

        # ── 2. حل الماركة ─────────────────────────────────────────────
        brand = self._resolve_brand(enriched, pname)
        if brand:
            enriched["الماركة"] = brand
            enriched["الماركة_الرسمية"] = brand

        # ── 3. تحديد التصنيف ──────────────────────────────────────────
        category = self._resolve_category(enriched)
        if category:
            enriched["تصنيف_المنتج"] = category
            enriched["التصنيف_الرسمي"] = category

        # ── 4. توليد الوصف الذهبي ─────────────────────────────────────
        self._ensure_golden_description(enriched, frag_data)

        # ── 5. ضمان SKU ──────────────────────────────────────────────
        self._ensure_sku(enriched)

        # العلم يعكس النجاح الفعلي (وصف+صورة+ماركة) لا «دائماً True» — وإلا
        # عبر منتجٌ ناقصٌ بوابةَ الجودة لمجرّد مروره بخط الإثراء.
        from services.enrichment_service import is_enriched
        enriched["_enriched"] = is_enriched(enriched)

        # تتبّع «لماذا غاب الحقل» (بيان فقط — لا يغيّر الإثراء ولا is_enriched).
        trace = _build_enrich_trace(enriched, frag_data, self._enrich_ai)
        if trace:
            enriched["_enrich_trace"] = trace
        return enriched

    def _fetch_fragrantica_safe(self, product_name: str) -> Dict:
        """استدعاء fetch_fragrantica_info مع حماية من الأخطاء."""
        try:
            from engines.ai_engine import fetch_fragrantica_info
            return fetch_fragrantica_info(product_name) or {}
        except Exception as exc:
            logger.warning("⚠️ فشل Fragrantica لـ «%s»: %s", product_name[:40], exc)
            return {"success": False, "_error": str(exc)[:120]}

    def _resolve_brand(self, product: Dict, pname: str) -> str:
        """حل الماركة: من الحقل → BrandManager → استخراج من الاسم."""
        # 1) الماركة من الحقل المباشر
        raw_brand = str(
            product.get("الماركة")
            or product.get("الماركة_الرسمية")
            or product.get("brand")
            or product.get("brand_name")
            or ""
        ).strip()

        # 2) مطابقة عبر BrandManager
        try:
            from utils.salla_shamel_export import _resolve_brand_safe
            resolved = _resolve_brand_safe(raw_brand)
            if resolved:
                return resolved
        except Exception as exc:
            logger.debug("BrandManager resolve failed: %s", exc)

        # 3) استخراج من الاسم كملاذ أخير
        if not raw_brand or raw_brand in ("غير متوفر", "غير محدد", "nan", "None"):
            try:
                from utils.salla_shamel_export import _brand_from_name
                extracted = _brand_from_name(pname)
                if extracted:
                    return extracted
            except Exception:
                pass

        return raw_brand or ""

    def _resolve_category(self, product: Dict) -> str:
        """حل التصنيف: تصنيف ذكي من اسم المنتج (عناية/مكياج/جسم/عطر) ← تدرّج.

        المُصنِّف الذكي أولاً لأن اسم المنتج أوثق إشارة، ويمنع فرض «عطور رجالية»
        على منتجات العناية/الجسم/المكياج (الخلل القديم: زبدة جسم ⇒ عطر رجالي).
        """
        # 0) تصنيف ذكي من الاسم — يطابق شجرة المتجر الحقيقية (قابل لمطابقة Make)
        try:
            from services.category_classifier import classify_category
            _pn = _get_name(product)
            _g = str(
                product.get("الجنس") or product.get("gender_hint")
                or product.get("gender") or ""
            ).strip()
            _br = str(
                product.get("الماركة") or product.get("الماركة_الرسمية")
                or product.get("brand") or ""
            ).strip()
            smart = classify_category(_pn, _g, _br)
            if smart:
                return smart
        except Exception:
            pass

        # 1) تصنيف صريح
        raw_cat = str(
            product.get("تصنيف_المنتج")
            or product.get("التصنيف_الرسمي")
            or product.get("التصنيف")
            or product.get("category")
            or ""
        ).strip()

        if raw_cat:
            try:
                from utils.salla_shamel_export import _sanitize_category_safe
                return _sanitize_category_safe(raw_cat, export_mode="safe")
            except Exception:
                return raw_cat

        # 2) استنتاج من الجنس أو الاسم
        try:
            from engines.ai_engine import auto_infer_category
            pname = _get_name(product)
            gender = str(
                product.get("الجنس")
                or product.get("gender_hint")
                or product.get("gender")
                or ""
            ).strip()
            return auto_infer_category(pname, gender)
        except Exception:
            return "العطور > عطور للجنسين"

    def _ensure_golden_description(self, product: Dict, frag_data: Dict) -> None:
        """يضمن وجود وصف بتنسيق مهووس — يولّده إن لم يوجد.

        الأولوية:
          1. وصف AI موجود مسبقاً (``وصف_AI``).
          2. وصف عادي يمر فحص مهووس.
          3. توليد جديد عبر ``generate_salla_html_description`` (قالب ثابت).
          4. توليد AI كامل عبر ``generate_mahwous_description`` (إن مفعّل).
        """
        from services.enrichment_service import is_mahwous_description

        # فحص الأوصاف الموجودة
        for key in ("وصف_AI", "الوصف", "description"):
            existing = str(product.get(key, "")).strip()
            if existing and is_mahwous_description(existing):
                product["الوصف"] = existing
                product["وصف_AI"] = existing
                return

        pname = _get_name(product)
        brand = str(product.get("الماركة", "غير متوفر")).strip()
        price = _safe_float(
            product.get("سعر_المنافس")
            or product.get("السعر_المقترح")
            or product.get("price")
            or 0,
        )

        # توليد من القالب الثابت (سريع، بدون API)
        try:
            from utils.salla_shamel_export import generate_salla_html_description
            desc = generate_salla_html_description(
                product_name=pname,
                brand_name=brand,
                gender=str(product.get("الجنس", "للجنسين")),
                size_ml=str(product.get("الحجم", "100")),
                fragrance_family=str(product.get("العائلة_العطرية", "غير متوفر")),
                top_notes=str(product.get("top_notes", "غير متوفر")),
                heart_notes=str(product.get("heart_notes", "غير متوفر")),
                base_notes=str(product.get("base_notes", "غير متوفر")),
                description_text=str(product.get("description_ar", "")),
                fragrance_notes=str(product.get("fragrance_notes", "")),
                category=str(product.get("تصنيف_المنتج", product.get("التصنيف", ""))),
            )

            if is_mahwous_description(desc):
                product["الوصف"] = desc
                product["وصف_AI"] = desc
                return
        except Exception as exc:
            logger.debug("فشل توليد الوصف الثابت: %s", exc)

        # توليد AI كامل (أبطأ، أعلى جودة)
        if self._enrich_ai and price > 0:
            try:
                from engines.ai_engine import generate_mahwous_description
                ai_desc = generate_mahwous_description(
                    pname, price,
                    fragrantica_data=frag_data if frag_data.get("success") else None,
                )
                if ai_desc and is_mahwous_description(str(ai_desc)):
                    product["الوصف"] = str(ai_desc)
                    product["وصف_AI"] = str(ai_desc)
                    return
            except Exception as exc:
                logger.debug("فشل توليد الوصف AI: %s", exc)

    @staticmethod
    def _ensure_sku(product: Dict) -> None:
        """ضمان وجود SKU فريد ثابت."""
        for key in ("رمز المنتج sku", "sku", "SKU"):
            if str(product.get(key, "")).strip():
                return
        try:
            from utils.salla_shamel_export import _generate_sku
            brand = str(product.get("الماركة", "")).strip()
            pname = _get_name(product)
            size = str(product.get("الحجم", "100")).strip()
            product["sku"] = _generate_sku(brand, pname, size)
        except Exception:
            pass

    # ══════════════════════════════════════════════════════════════════════
    #  المرحلة 3: بوابة الجودة الذهبية
    # ══════════════════════════════════════════════════════════════════════
    @staticmethod
    def _apply_quality_gate(
        products: List[Dict],
    ) -> tuple[List[Dict], List[Dict]]:
        """تطبيق بوابة الجودة: وصف مهووس + صورة حقيقية + ماركة + سعر > 0.

        Returns:
            (passed_list, rejected_list)
        """
        from services.enrichment_service import is_enriched, gate_reason, GATE_REASON_PRICE

        passed, rejected = [], []
        for p in products:
            # فحص السعر
            price = _safe_float(
                p.get("سعر_المنافس")
                or p.get("السعر_المقترح")
                or p.get("price")
                or p.get("comp_price")
                or 0,
            )
            if price <= 0:
                p["_gate_reason"] = GATE_REASON_PRICE
                rejected.append(p)
                logger.debug("🚫 مرفوض (سعر ≤ 0): «%s»", _get_name(p)[:40])
                continue

            if is_enriched(p):
                passed.append(p)
            else:
                p["_gate_reason"] = gate_reason(p)
                rejected.append(p)
                logger.debug("🚫 مرفوض (%s): «%s»", _gate_reason_detail(p), _get_name(p)[:40])

        return passed, rejected

    # ══════════════════════════════════════════════════════════════════════
    #  المرحلة 4: التصدير
    # ══════════════════════════════════════════════════════════════════════
    def _execute_export(
        self,
        enriched_products: List[Dict],
        target: str,
        output_path: str,
        source: str,
    ) -> Dict[str, Any]:
        """تنفيذ التصدير: ملف سلة CSV أو إرسال Make.com.

        Args:
            enriched_products: المنتجات المُثراة التي عبرت البوابة.
            target: ``"salla"`` أو ``"make"``.
            output_path: مسار ملف التصدير (لسلة فقط).
            source: مصدر البيانات (``"queue"`` أو ``"dataframe"``).

        Returns:
            dict بنتيجة التصدير.
        """
        if target == "salla":
            return self._export_salla(enriched_products, output_path)
        elif target == "make":
            return self._export_make(enriched_products, source)
        else:
            return {"success": False, "message": f"❌ هدف تصدير غير معروف: {target}"}

    def _export_salla(self, products: List[Dict], output_path: str) -> Dict[str, Any]:
        """تصدير المنتجات إلى ملف سلة CSV (40 عمود).

        يستخدم ``build_salla_shamel_dataframe`` لبناء DataFrame كامل،
        ثم ``export_to_salla_shamel_csv`` لتوليد الملف.
        """
        try:
            # تحويل القائمة إلى DataFrame لتمريره لدالة سلة
            df = pd.DataFrame(products)

            from utils.salla_shamel_export import export_to_salla_shamel_csv
            csv_bytes, count, found_df = export_to_salla_shamel_csv(
                df, self._catalog, verify_missing=True, export_mode="safe",
            )

            if count == 0:
                return {
                    "success": False,
                    "message": "⚠️ لا منتجات صالحة بعد فلترة سلة",
                    "count": 0,
                }

            # حفظ الملف إذا حُدد المسار
            if output_path:
                with open(output_path, "wb") as f:
                    f.write(csv_bytes)
                logger.info("💾 تم حفظ ملف سلة: %s (%d منتج)", output_path, count)

            return {
                "success": True,
                "message": f"✅ تم تصدير {count} منتج إلى ملف سلة CSV",
                "count": count,
                "csv_bytes": csv_bytes,
                "path": output_path or "(BytesIO)",
                "found_in_catalog": len(found_df) if not found_df.empty else 0,
            }
        except Exception as exc:
            logger.error("❌ فشل تصدير سلة: %s", exc)
            return {"success": False, "message": f"❌ فشل تصدير سلة: {exc}", "count": 0}

    def _export_make(self, products: List[Dict], source: str) -> Dict[str, Any]:
        """إرسال المنتجات عبر Make.com webhook.

        يستخدم ``send_batch_smart`` للإرسال بدفعات مع retry.
        بعد النجاح يُحدّث حالة الطابور إن كان المصدر ``"queue"``.
        """
        try:
            # تجهيز المنتجات بصيغة Make.com
            make_products = []
            for p in products:
                make_products.append({
                    "name": _get_name(p),
                    "المنتج": _get_name(p),
                    "منتج_المنافس": _get_name(p),
                    "أسم المنتج": _get_name(p),
                    "سعر_المنافس": _safe_float(
                        p.get("سعر_المنافس") or p.get("comp_price") or 0,
                    ),
                    "price": _safe_float(
                        p.get("السعر_المقترح") or p.get("price") or 0,
                    ),
                    "الماركة": str(p.get("الماركة", "")).strip(),
                    "brand": str(p.get("الماركة", "")).strip(),
                    "الوصف": str(p.get("الوصف", p.get("وصف_AI", ""))).strip(),
                    "description": str(p.get("الوصف", p.get("وصف_AI", ""))).strip(),
                    "image_url": str(
                        p.get("صورة_المنافس") or p.get("صورة المنتج") or p.get("image_url") or ""
                    ).strip(),
                    "صورة المنتج": str(
                        p.get("صورة_المنافس") or p.get("صورة المنتج") or p.get("image_url") or ""
                    ).strip(),
                    "sku": str(p.get("sku", p.get("رمز المنتج sku", ""))).strip(),
                    "رمز المنتج sku": str(p.get("sku", p.get("رمز المنتج sku", ""))).strip(),
                    "التصنيف": str(p.get("تصنيف_المنتج", p.get("التصنيف_الرسمي", ""))).strip(),
                    "category_name": str(p.get("تصنيف_المنتج", p.get("التصنيف_الرسمي", ""))).strip(),
                    "اسم التصنيف": str(p.get("تصنيف_المنتج", p.get("التصنيف_الرسمي", ""))).strip(),
                    "brand_id": p.get("brand_id", ""),
                    "category_id": p.get("category_id", ""),
                    # ── صور متعددة + كمية + مكونات/مواصفات (يقرأها make_helper) ──
                    "صور المنتج": p.get("صور المنتج", p.get("image_urls", "")),
                    "image_urls": p.get("image_urls", p.get("صور المنتج", "")),
                    "الكمية": p.get("الكمية", p.get("quantity", "")),
                    "المكونات": p.get("المكونات", ""),
                    "المواصفات": p.get("المواصفات", ""),
                    "top_notes": p.get("top_notes", ""),
                    "heart_notes": p.get("heart_notes", p.get("middle_notes", "")),
                    "base_notes": p.get("base_notes", ""),
                    "الحجم": p.get("الحجم", p.get("size", "")),
                    "الجنس": p.get("الجنس", p.get("gender", "")),
                    "العائلة_العطرية": p.get("العائلة_العطرية", p.get("fragrance_family", "")),
                    # مفاتيح الطابور (لتحديث الحالة بعد النجاح)
                    "_product_key": p.get("product_key", ""),
                })

            # ── بوابة منع التكرار بالبصمة (catalog ← sent) قبل الإرسال ──
            dedup = {"new": len(make_products), "likely": 0, "match": 0}
            if self._skip_dedup:
                # إرسال متعمّد رغم التشابه (قرار المستخدم) — لا نحجب.
                logger.info("⏭️ تخطّي بوابة التكرار بقرار المستخدم (%d منتج)",
                            len(make_products))
            else:
                try:
                    from utils.missing_queue_manager import screen_for_duplicates
                    screen = screen_for_duplicates(make_products, self._catalog)
                    make_products = screen.kept
                    dedup = screen.summary
                    if dedup.get("match") or dedup.get("likely"):
                        logger.info(
                            "🛡️ منع التكرار: %d مكرر مُسقَط · %d محتمل محجوز · %d جديد",
                            dedup.get("match", 0), dedup.get("likely", 0), dedup.get("new", 0),
                        )
                except Exception as exc:
                    logger.warning("⚠️ تخطّي بوابة التكرار (fail-open): %s", exc)

            if not make_products:
                return {
                    "success": True,
                    "message": (f"لا منتجات جديدة للإرسال — "
                                f"تكرار {dedup.get('match',0)} · محتمل {dedup.get('likely',0)}"),
                    "sent": 0, "failed": 0, "count": 0, "dedup": dedup,
                }

            from utils.make_helper import send_batch_smart
            result = send_batch_smart(
                make_products,
                batch_type="new",
                batch_size=self._batch_size,
                confidence_filter="",  # لا حاجة — فلترنا مسبقاً
            )

            # تحديث حالة الطابور عند النجاح
            if source == "queue" and result.get("success"):
                self._mark_queue_sent(products, success=True)
            elif source == "queue" and not result.get("success"):
                self._mark_queue_sent(products, success=False, error=result.get("message", ""))

            return {
                "success": result.get("success", False),
                "message": result.get("message", ""),
                "sent": result.get("sent", 0),
                "failed": result.get("failed", 0),
                "count": result.get("sent", 0),
                "dedup": dedup,
            }
        except Exception as exc:
            logger.error("❌ فشل إرسال Make.com: %s", exc)
            return {"success": False, "message": f"❌ فشل إرسال Make.com: {exc}", "count": 0}

    @staticmethod
    def _mark_queue_sent(products: List[Dict], success: bool, error: str = "") -> None:
        """تحديث حالة الطابور بعد الإرسال."""
        try:
            from utils.missing_queue_manager import mark_products_sent
            keys = [p.get("product_key", "") for p in products if p.get("product_key")]
            if keys:
                count = mark_products_sent(keys, success=success, error=error)
                logger.info("📝 تحديث حالة الطابور: %d منتج ← %s",
                            count, "sent_success" if success else "sent_failed")
        except Exception as exc:
            logger.warning("⚠️ فشل تحديث حالة الطابور: %s", exc)


# ══════════════════════════════════════════════════════════════════════════
#  دوال مساعدة (Module-level Helpers)
# ══════════════════════════════════════════════════════════════════════════

def _now() -> str:
    """طابع زمني مُنسَّق."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _get_name(p: Dict) -> str:
    """يستخرج اسم المنتج من قاموس — يبحث في عدة مفاتيح محتملة."""
    for key in ("منتج_المنافس", "أسم المنتج", "اسم المنتج", "المنتج",
                "product_name", "name", "title"):
        val = str(p.get(key, "")).strip()
        if val and val.lower() not in ("nan", "none", ""):
            return val
    return ""


def _safe_float(val: Any, default: float = 0.0) -> float:
    """تحويل آمن إلى float."""
    try:
        if val is None or str(val).strip() in ("", "nan", "None", "NaN"):
            return default
        return float(val)
    except (ValueError, TypeError):
        return default


def _gate_reason_counts(rejected: List[Dict]) -> Dict[str, int]:
    """عدّاد أسباب رفض البوابة (لكل سبب) من قائمة المرفوضات — مفاتيحه الأسباب الأربعة."""
    from services.enrichment_service import (
        GATE_REASON_IMAGE, GATE_REASON_BRAND, GATE_REASON_DESC, GATE_REASON_PRICE,
    )
    counts = {
        GATE_REASON_IMAGE: 0,
        GATE_REASON_BRAND: 0,
        GATE_REASON_DESC: 0,
        GATE_REASON_PRICE: 0,
    }
    for p in rejected:
        r = p.get("_gate_reason", "")
        if r in counts:
            counts[r] += 1
    return counts


def _format_gate_reasons(counts: Dict[str, int]) -> str:
    """نص تفصيلي لأسباب الرفض (للتقرير وواجهة الإرسال) — يستبدل الرسالة العامة."""
    from services.enrichment_service import (
        GATE_REASON_IMAGE, GATE_REASON_BRAND, GATE_REASON_DESC, GATE_REASON_PRICE,
    )
    return (
        "❌ لم يعبر البوابة: "
        f"{counts.get(GATE_REASON_IMAGE, 0)} لا صورة · "
        f"{counts.get(GATE_REASON_BRAND, 0)} لا ماركة · "
        f"{counts.get(GATE_REASON_DESC, 0)} وصف قصير · "
        f"{counts.get(GATE_REASON_PRICE, 0)} سعر صفر"
    )


def _build_enrich_trace(enriched: Dict, frag_data: Dict, enrich_ai: bool) -> Dict[str, str]:
    """يصف سبب غياب كل حقل بعد خط الإثراء — بيان فقط، لا يغيّر أي منطق إثراء.

    يفحص نتيجة الإثراء (Fragrantica + إسناد الصورة + الماركة + الوصف الذهبي)
    ويُرجع ``{field: reason}`` للحقول الناقصة فقط؛ النجاح الكامل ⇒ dict فارغ.
    المفاتيح: image / notes / brand / desc — تُقرأ بجانب سبب البوابة في التقرير.
    """
    from services.enrichment_service import is_mahwous_description

    trace: Dict[str, str] = {}
    frag_ok = bool(frag_data.get("success"))
    frag_err = str(frag_data.get("_error") or "").strip()

    def _src_reason(no_field_msg: str) -> str:
        if not enrich_ai:
            return "الإثراء AI معطّل"
        if not frag_ok:
            return f"فشل جلب المصدر: {frag_err}" if frag_err else "فشل جلب المصدر (Fragrantica)"
        return no_field_msg

    # صورة (نفس مفاتيح is_enriched)
    image = str(
        enriched.get("صورة_المنافس") or enriched.get("صورة المنتج")
        or enriched.get("image_url") or ""
    ).strip()
    if not image:
        trace["image"] = _src_reason("المصدر لم يُرجع صورة")

    # نوتات (تغذّي الوصف الذهبي)
    has_notes = any(
        str(enriched.get(k, "")).strip()
        for k in ("top_notes", "heart_notes", "base_notes")
    )
    if not has_notes:
        trace["notes"] = _src_reason("المصدر لم يُرجع نوتات")

    # ماركة (نفس مفاتيح is_enriched)
    brand = str(
        enriched.get("الماركة") or enriched.get("الماركة_الرسمية")
        or enriched.get("brand") or ""
    ).strip()
    if not brand:
        trace["brand"] = "تعذّرت مطابقة الماركة ولم تُستخرَج من الاسم"

    # وصف بتنسيق مهووس
    desc = str(enriched.get("الوصف") or enriched.get("وصف_AI") or "")
    if not is_mahwous_description(desc):
        price = _safe_float(
            enriched.get("سعر_المنافس") or enriched.get("السعر_المقترح")
            or enriched.get("price") or 0
        )
        if not has_notes:
            trace["desc"] = "لا نوتات لبناء الوصف الذهبي"
        elif enrich_ai and price <= 0:
            trace["desc"] = "لا سعر ⇒ تخطّي توليد الوصف بالذكاء"
        elif not enrich_ai:
            trace["desc"] = "القالب لم ينتج تنسيق مهووس (الذكاء معطّل)"
        else:
            trace["desc"] = "فشل توليد الوصف الذهبي"
    return trace


def _gate_reason_detail(p: Dict) -> str:
    """يدمج سبب البوابة مع تتبّع الإثراء المقابل: «لا صورة حقيقية ← المصدر لم يُرجع صورة»."""
    from services.enrichment_service import (
        GATE_REASON_IMAGE, GATE_REASON_DESC, GATE_REASON_BRAND,
    )
    reason = str(p.get("_gate_reason", "")).strip()
    trace = p.get("_enrich_trace") or {}
    key = {
        GATE_REASON_IMAGE: "image",
        GATE_REASON_DESC: "desc",
        GATE_REASON_BRAND: "brand",
    }.get(reason)
    detail = trace.get(key) if key else ""
    return f"{reason} ← {detail}" if detail else reason


def _format_gate_traces(rejected: List[Dict]) -> str:
    """أسطر «سبب البوابة ← سبب الإثراء» المميّزة مع عددها (تشخيص الصندوق الزجاجي)."""
    pairs: Dict[str, int] = {}
    for p in rejected:
        detail = _gate_reason_detail(p)
        if "←" in detail:  # فقط ما اقترن بتتبّع إثراء
            pairs[detail] = pairs.get(detail, 0) + 1
    if not pairs:
        return ""
    ordered = sorted(pairs.items(), key=lambda kv: kv[1], reverse=True)
    return "\n".join(f"   ↳ {pair} ({n})" for pair, n in ordered)


def _merge_frag(target: Dict, frag: Dict) -> None:
    """يدمج بيانات Fragrantica في قاموس المنتج."""
    if not frag or not frag.get("success"):
        return
    _map = {
        "top_notes":        lambda v: ", ".join(v) if isinstance(v, list) else str(v or ""),
        "middle_notes":     lambda v: ", ".join(v) if isinstance(v, list) else str(v or ""),
        "base_notes":       lambda v: ", ".join(v) if isinstance(v, list) else str(v or ""),
        "fragrance_family": str,
        "brand":            None,  # يُعالج بشكل منفصل في _resolve_brand
        "description_ar":   str,
        "image_url":        str,
        "designer":         str,
        "year":             str,
        "type":             str,
        "size":             str,
    }
    for key, transform in _map.items():
        val = frag.get(key)
        if val and transform:
            transformed = transform(val)
            if transformed and transformed not in ("غير متوفر", "None", "nan"):
                target[key] = transformed

    # تحويل heart_notes من middle_notes (مصطلح Fragrantica)
    if "middle_notes" in target:
        target["heart_notes"] = target.pop("middle_notes")

    # العائلة العطرية → حقل عربي
    if frag.get("fragrance_family"):
        target["العائلة_العطرية"] = str(frag["fragrance_family"])

    # صورة Fragrantica → حقل الصورة (إذا لم تكن موجودة)
    if frag.get("image_url") and not target.get("صورة_المنافس"):
        target["صورة_المنافس"] = str(frag["image_url"])
