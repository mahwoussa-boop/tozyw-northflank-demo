"""services/ai_redistributor.py — محرك إعادة توزيع المنتجات العالقة بالذكاء الاصطناعي.

يسحب المنتجات من الأقسام الثلاثة (مراجعة، مستبعدة، مفقودة)،
يمرّرها عبر وكلاء AI لاتخاذ قرارات حاسمة، ويُعيد خطة توزيع
(RedistributionPlan) دون تغيير البيانات مباشرةً — المستخدم يعتمدها.

القواعد الصارمة:
  1. لا DELETE في SQL — UPDATE فقط لتغيير الحالة.
  2. دفعات ≤20 منتجاً في الطلب الواحد.
  3. حد أقصى 200 منتج لكل قسم في الجولة الواحدة.
  4. try/except لكل منتج — لا انهيار متسلسل.
  5. عتبة الثقة = 0.85 (موحّدة مع باقي النظام).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, Optional

import pandas as pd

from infrastructure.ai_decisions_repo import AIDecision, AIDecisionRepository
from services.ai_service import AIService

logger = logging.getLogger(__name__)

# ── ثوابت ──────────────────────────────────────────────────────────────
BATCH_SIZE = 20           # حجم الدفعة المُرسلة لـ AI
MAX_PER_SECTION = 200     # حد أقصى لكل قسم في الجولة الواحدة
CONFIDENCE_THRESHOLD = 0.85

# بادئة النص البديل الذي يكتبه محرّك المطابقة في «منتج_المنافس» حين لا يجد أي
# مرشّح لمنتجنا (engines/engine.py:2300: «❌ لم يتجاوز فلاتر المطابقة / لا يوجد»).
# هذا ليس اسم منتج منافس بل علامة «منتجنا بلا مطابقة» — يُمنع نقله إلى «مفقود».
_PLACEHOLDER_PREFIX = "❌"


def _is_placeholder_name(comp_name: str) -> bool:
    """هل «منتج_المنافس» نص بديل (لا اسم منافس حقيقي)؟ فارغ أو يبدأ بـ❌."""
    s = (comp_name or "").strip()
    return not s or s.startswith(_PLACEHOLDER_PREFIX)


# ═══════════════════════════════════════════════════════════════════════
#  هياكل البيانات
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class ProductMove:
    """حركة مقترحة لمنتج واحد (من قسم → قسم)."""
    product_name: str
    product_id: str
    old_section: str       # القسم الحالي
    new_section: str       # القسم المقترح
    ai_reason: str         # سبب القرار من AI
    ai_confidence: float   # نسبة الثقة (0-1)
    row_index: int         # فهرس الصف في DataFrame الأصلي


@dataclass
class RedistributionPlan:
    """خطة إعادة توزيع كاملة (وضع المحاكاة — Dry-Run).

    تُعرض للمستخدم قبل التنفيذ. تحتوي كل الحركات المقترحة
    مع إحصاءات شاملة.
    """
    moves: list[ProductMove] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    batch_id: str = ""

    # إحصاءات مفصّلة لكل مرحلة
    reviews_processed: int = 0
    reviews_upgraded: int = 0
    reviews_to_missing: int = 0
    reviews_to_excluded: int = 0
    reviews_stayed: int = 0

    excluded_processed: int = 0
    excluded_salvaged: int = 0
    excluded_to_missing: int = 0
    excluded_locked: int = 0

    missing_processed: int = 0
    missing_deduplicated: int = 0
    missing_confirmed: int = 0

    @property
    def total_moved(self) -> int:
        """إجمالي المنتجات التي ستتحرك."""
        return len([m for m in self.moves if m.old_section != m.new_section])

    @property
    def total_processed(self) -> int:
        return self.reviews_processed + self.excluded_processed + self.missing_processed

    @property
    def is_empty(self) -> bool:
        return len(self.moves) == 0


@dataclass
class RedistributionReport:
    """تقرير التنفيذ النهائي (بعد اعتماد المستخدم)."""
    applied_moves: int = 0
    decisions_logged: int = 0
    errors: list[str] = field(default_factory=list)
    batch_id: str = ""


# ═══════════════════════════════════════════════════════════════════════
#  المحرك الأساسي
# ═══════════════════════════════════════════════════════════════════════

class AIRedistributor:
    """محرك إعادة توزيع المنتجات العالقة بالذكاء الاصطناعي.

    يعمل بنمط Dry-Run:
      1. ``build_plan()``  — يحلّل ويبني خطة مقترحة (بلا تغيير).
      2. ``apply_plan()``  — ينفّذ الخطة على البيانات بعد اعتماد المستخدم.
    """

    def __init__(
        self,
        ai_service: AIService,
        decisions_repo: AIDecisionRepository,
        *,
        confidence_threshold: float = CONFIDENCE_THRESHOLD,
        batch_size: int = BATCH_SIZE,
        max_per_section: int = MAX_PER_SECTION,
    ) -> None:
        self._ai = ai_service
        self._repo = decisions_repo
        self._threshold = confidence_threshold
        self._batch_size = batch_size
        self._max_per_section = max_per_section

    # ── المرحلة 1: بناء الخطة (Dry-Run) ──────────────────────────────

    def build_plan(
        self,
        sections: dict[str, pd.DataFrame],
        missing_df: Optional[pd.DataFrame] = None,
        our_catalog: Optional[pd.DataFrame] = None,
        *,
        progress_cb: Optional[Callable[[str, int, int], None]] = None,
    ) -> RedistributionPlan:
        """يبني خطة إعادة توزيع كاملة بدون تطبيق أي تغيير.

        Args:
            sections: أقسام المنتجات من AppState.
            missing_df: DataFrame المنتجات المفقودة.
            our_catalog: كتالوجنا (لكشف التكرار في المفقودات).
            progress_cb: دالة تقدم ``(stage_name, processed, total)``.

        Returns:
            RedistributionPlan مع كل الحركات المقترحة.
        """
        plan = RedistributionPlan(
            batch_id=AIDecisionRepository.new_batch_id(),
        )

        # ── مرحلة 1: معالجة المراجعة ──
        self._process_reviews(sections, plan, progress_cb)

        # ── مرحلة 2: معالجة المستبعدات ──
        self._process_excluded(sections, plan, progress_cb)

        # ── مرحلة 3: معالجة المفقودات ──
        self._process_missing(missing_df, our_catalog, plan, progress_cb)

        return plan

    # ── المرحلة 2: تنفيذ الخطة (بعد اعتماد المستخدم) ─────────────────

    def apply_plan(
        self,
        plan: RedistributionPlan,
        sections: dict[str, pd.DataFrame],
        missing_df: Optional[pd.DataFrame] = None,
    ) -> tuple[dict[str, pd.DataFrame], Optional[pd.DataFrame], RedistributionReport]:
        """ينفّذ خطة إعادة التوزيع المعتمدة على البيانات.

        يُحدّث sections و missing_df حسب الحركات المعتمدة،
        ويُسجّل كل قرار في ai_decisions_log.

        Args:
            plan: الخطة المعتمدة من المستخدم.
            sections: أقسام المنتجات (ستُعدَّل in-place).
            missing_df: المفقودات (ستُعدَّل إن لزم).

        Returns:
            (sections_updated, missing_df_updated, report)
        """
        report = RedistributionReport(batch_id=plan.batch_id)

        # ── تجميع الحركات حسب القسم القديم والجديد ──
        # Structure: {old_section: {new_section: [row_indices]}}
        move_map: dict[str, dict[str, list[int]]] = {}
        for move in plan.moves:
            if move.old_section == move.new_section:
                continue  # لا حركة فعلية
            move_map.setdefault(move.old_section, {}).setdefault(
                move.new_section, [],
            ).append(move.row_index)

        # ── تطبيق الحركات على sections ──
        for old_sec, targets in move_map.items():
            if old_sec == "missing":
                # الحركة من missing_df
                if missing_df is None or missing_df.empty:
                    continue
                # نجمع المؤشرات ونحذف مرّة واحدة في النهاية: الحذف المتتالي مع
                # reset_index يُفسد مواضع الدورات التالية فينتقل صفّ خاطئ.
                all_moved_indices: list[int] = []
                for new_sec, indices in targets.items():
                    valid_idx = [i for i in indices if i < len(missing_df)]
                    if not valid_idx:
                        continue
                    moved_rows = missing_df.iloc[valid_idx].copy()
                    all_moved_indices.extend(valid_idx)
                    # إضافة للقسم الجديد
                    if new_sec in sections:
                        sections[new_sec] = pd.concat(
                            [sections[new_sec], moved_rows], ignore_index=True,
                        )
                    else:
                        sections[new_sec] = moved_rows
                    report.applied_moves += len(valid_idx)
                # حذف كل المنقول دفعةً واحدة (المؤشرات الأصلية تبقى سليمة)
                if all_moved_indices:
                    _uniq = sorted(set(all_moved_indices), reverse=True)
                    missing_df = missing_df.drop(
                        missing_df.index[_uniq],
                    ).reset_index(drop=True)
            else:
                # الحركة من sections
                source_df = sections.get(old_sec, pd.DataFrame())
                if source_df.empty:
                    continue
                all_moved_indices: list[int] = []
                for new_sec, indices in targets.items():
                    valid_idx = [i for i in indices if i < len(source_df)]
                    if not valid_idx:
                        continue
                    moved_rows = source_df.iloc[valid_idx].copy()
                    all_moved_indices.extend(valid_idx)

                    if new_sec == "missing":
                        # ختم مستوى الثقة «review» (محتمل) صراحةً: صفوف الأقسام
                        # الأخرى بلا عمود «مستوى_الثقة» فتظهر «مشكوك» بلا هذا الختم.
                        # إعادة التوزيع لا تُنتج «مؤكداً» — أعلى قيمة صادقة «محتمل».
                        moved_rows["مستوى_الثقة"] = "review"
                        # إضافة لـ missing_df
                        if missing_df is not None and not missing_df.empty:
                            missing_df = pd.concat(
                                [missing_df, moved_rows], ignore_index=True,
                            )
                        else:
                            missing_df = moved_rows.copy()
                    elif new_sec in sections:
                        sections[new_sec] = pd.concat(
                            [sections[new_sec], moved_rows], ignore_index=True,
                        )
                    else:
                        sections[new_sec] = moved_rows
                    report.applied_moves += len(valid_idx)

                # إزالة الصفوف المنقولة من القسم المصدر
                if all_moved_indices:
                    unique_indices = sorted(set(all_moved_indices), reverse=True)
                    remaining = source_df.drop(
                        source_df.index[unique_indices],
                    ).reset_index(drop=True)
                    sections[old_sec] = remaining

        # ── تسجيل القرارات في قاعدة البيانات ──
        for move in plan.moves:
            if move.old_section == move.new_section:
                continue
            try:
                decision = AIDecision(
                    product_name=move.product_name,
                    product_id=move.product_id,
                    ai_reason=move.ai_reason,
                    ai_confidence=move.ai_confidence,
                    ai_decision=move.new_section,
                    section=move.old_section,
                    user_action="auto_redistributed",
                    batch_id=plan.batch_id,
                )
                self._repo.log_decision(decision)
                report.decisions_logged += 1
            except Exception as exc:
                report.errors.append(
                    f"فشل تسجيل قرار «{move.product_name}»: {exc}"
                )

        return sections, missing_df, report

    # ═══════════════════════════════════════════════════════════════════
    #  أدوات مساعدة
    # ═══════════════════════════════════════════════════════════════════

    @staticmethod
    def _get_match_ratio(row: pd.Series) -> float:
        """يستخرج نسبة التطابق من الصف (0-100). يُرجع -1 إن لم تُوجد."""
        for col in ("نسبة_التطابق", "match_ratio", "التطابق"):
            val = row.get(col) if hasattr(row, "get") else None
            if val is not None:
                try:
                    return float(val)
                except (TypeError, ValueError):
                    pass
        return -1.0

    @staticmethod
    def _get_price_section(row: pd.Series) -> str:
        """يحدد القسم السعري المناسب بناءً على فرق السعر."""
        try:
            our = float(row.get("السعر", 0) or 0)
            comp = float(row.get("سعر_المنافس", 0) or 0)
            if our <= 0 or comp <= 0:
                return "approved"
            diff = comp - our
            if diff > 0:
                return "price_raise"
            if diff < 0:
                return "price_lower"
            return "approved"
        except (TypeError, ValueError):
            return "approved"

    # ═══════════════════════════════════════════════════════════════════
    #  مسارات العمل الداخلية
    # ═══════════════════════════════════════════════════════════════════

    def _process_reviews(
        self,
        sections: dict[str, pd.DataFrame],
        plan: RedistributionPlan,
        progress_cb: Optional[Callable] = None,
    ) -> None:
        """مسار 1: معالجة منتجات «تحت المراجعة».

        تحليل حسابي على نسبة_التطابق:
          ≥ 85%  → مؤكد مطابق → القسم السعري المناسب
          ≤ 30%  → غير مطابق → مستبعد
          31-84% → منطقة رمادية → يبقى في المراجعة
        """
        review_df = sections.get("review", pd.DataFrame())
        if review_df.empty:
            return

        to_process = review_df.iloc[: self._max_per_section]
        total = len(to_process)
        plan.reviews_processed = total

        if progress_cb:
            progress_cb("مراجعة", 0, total)

        for idx in range(total):
            try:
                row = to_process.iloc[idx]
                original_idx = to_process.index[idx]
                ratio = self._get_match_ratio(row)
                name = str(row.get("المنتج", row.get("منتج_المنافس", "—")))
                pid = str(row.get("معرف_المنتج", ""))

                if ratio >= 85:
                    target = self._get_price_section(row)
                    plan.moves.append(ProductMove(
                        product_name=name, product_id=pid,
                        old_section="review", new_section=target,
                        ai_reason=f"نسبة تطابق عالية ({ratio:.0f}%) — مؤكد حسابياً",
                        ai_confidence=min(ratio / 100, 1.0),
                        row_index=original_idx,
                    ))
                    plan.reviews_upgraded += 1
                elif 0 <= ratio <= 30:
                    plan.moves.append(ProductMove(
                        product_name=name, product_id=pid,
                        old_section="review", new_section="excluded",
                        ai_reason=f"نسبة تطابق منخفضة ({ratio:.0f}%) — غير مطابق",
                        ai_confidence=max(1.0 - ratio / 100, 0.7),
                        row_index=original_idx,
                    ))
                    plan.reviews_to_excluded += 1
                else:
                    plan.reviews_stayed += 1

            except Exception as exc:
                plan.errors.append(f"خطأ في تحليل مراجعة [{idx}]: {exc}")
                plan.reviews_stayed += 1

            if progress_cb and idx % 20 == 0:
                progress_cb("مراجعة", idx + 1, total)

        if progress_cb:
            progress_cb("مراجعة", total, total)

    def _process_excluded(
        self,
        sections: dict[str, pd.DataFrame],
        plan: RedistributionPlan,
        progress_cb: Optional[Callable] = None,
    ) -> None:
        """مسار 2: معالجة المنتجات المستبعدة.

        تحليل حسابي:
          نسبة ≥ 85%       → خطأ في الاستبعاد → أعده للمراجعة
          نسبة ≤ 15% + اسم → مفقود حقيقي → missing
          لا اسم منتجنا    → مفقود → missing
          باقي             → يبقى مستبعد
        """
        excluded_df = sections.get("excluded", pd.DataFrame())
        if excluded_df.empty:
            return

        to_process = excluded_df.iloc[: self._max_per_section]
        total = len(to_process)
        plan.excluded_processed = total

        if progress_cb:
            progress_cb("مستبعد", 0, total)

        for idx in range(total):
            try:
                row = to_process.iloc[idx]
                original_idx = to_process.index[idx]
                ratio = self._get_match_ratio(row)
                name = str(row.get("المنتج", row.get("منتج_المنافس", "—")))
                pid = str(row.get("معرف_المنتج", ""))
                our_name = str(row.get("المنتج", "")).strip()
                comp_name = str(row.get("منتج_المنافس", "")).strip()

                if ratio >= 85:
                    plan.moves.append(ProductMove(
                        product_name=name, product_id=pid,
                        old_section="excluded", new_section="review",
                        ai_reason=f"تطابق مرتفع ({ratio:.0f}%) لكنه مستبعد — يحتاج مراجعة",
                        ai_confidence=min(ratio / 100, 1.0),
                        row_index=original_idx,
                    ))
                    plan.excluded_salvaged += 1
                elif 0 <= ratio <= 15 and comp_name and not _is_placeholder_name(comp_name):
                    plan.moves.append(ProductMove(
                        product_name=comp_name or name, product_id=pid,
                        old_section="excluded", new_section="missing",
                        ai_reason=f"نسبة تطابق ضئيلة ({ratio:.0f}%) — مفقود حقيقي",
                        ai_confidence=max(1.0 - ratio / 100, 0.85),
                        row_index=original_idx,
                    ))
                    plan.excluded_to_missing += 1
                elif ratio < 0 and not our_name and comp_name and not _is_placeholder_name(comp_name):
                    plan.moves.append(ProductMove(
                        product_name=comp_name, product_id=pid,
                        old_section="excluded", new_section="missing",
                        ai_reason="لا يوجد منتج مطابق في كتالوجنا — مفقود",
                        ai_confidence=0.9,
                        row_index=original_idx,
                    ))
                    plan.excluded_to_missing += 1
                else:
                    plan.excluded_locked += 1

            except Exception as exc:
                plan.errors.append(f"خطأ في تحليل مستبعد [{idx}]: {exc}")
                plan.excluded_locked += 1

            if progress_cb and idx % 20 == 0:
                progress_cb("مستبعد", idx + 1, total)

        if progress_cb:
            progress_cb("مستبعد", total, total)

    def _process_missing(
        self,
        missing_df: Optional[pd.DataFrame],
        our_catalog: Optional[pd.DataFrame],
        plan: RedistributionPlan,
        progress_cb: Optional[Callable] = None,
    ) -> None:
        """مسار 3: مراجعة المفقودات لكشف التكرار المخفي.

        يُعالج فقط غير المؤكدة. يستخدم مطابقة ضبابية (fuzzy).
        """
        if missing_df is None or missing_df.empty:
            return

        conf_col = "مستوى_الثقة"
        if conf_col in missing_df.columns:
            level = missing_df[conf_col].astype(str)
            non_confirmed = missing_df[level != "green"]
        else:
            non_confirmed = missing_df

        if non_confirmed.empty:
            return

        to_process = non_confirmed.iloc[: self._max_per_section]
        total = len(to_process)
        plan.missing_processed = total

        if progress_cb:
            progress_cb("مفقودات", 0, total)

        if our_catalog is None or our_catalog.empty:
            plan.missing_confirmed = total
            if progress_cb:
                progress_cb("مفقودات", total, total)
            return

        try:
            from rapidfuzz import fuzz, process as rf_process

            our_names_col = None
            for col in ("المنتج", "product_name", "name"):
                if col in our_catalog.columns:
                    our_names_col = col
                    break
            if our_names_col is None:
                plan.missing_confirmed = total
                if progress_cb:
                    progress_cb("مفقودات", total, total)
                return

            our_names_list = [
                str(n).strip().lower()
                for n in our_catalog[our_names_col].dropna()
                if str(n).strip()
            ]
            our_names_set = set(our_names_list)

            missing_name_col = None
            for col in ("منتج_المنافس", "المنتج", "product_name"):
                if col in to_process.columns:
                    missing_name_col = col
                    break
            if missing_name_col is None:
                plan.missing_confirmed = total
                if progress_cb:
                    progress_cb("مفقودات", total, total)
                return

            dedup_count = 0
            skipped_empty = 0          # ← أسماء فارغة: ليست منتجات مؤكدة
            for idx in range(len(to_process)):
                try:
                    row = to_process.iloc[idx]
                    name = str(row.get(missing_name_col, "")).strip().lower()
                    if not name:
                        skipped_empty += 1
                        continue

                    # 1) مطابقة دقيقة
                    if name in our_names_set:
                        plan.moves.append(ProductMove(
                            product_name=str(row.get(missing_name_col, "—")),
                            product_id=str(row.get("معرف_المنافس", row.get("معرف_المنتج", ""))),
                            old_section="missing", new_section="review",
                            ai_reason="تكرار مخفي — اسم مطابق تماماً في كتالوجنا",
                            ai_confidence=1.0,
                            row_index=to_process.index[idx],
                        ))
                        dedup_count += 1
                        continue

                    # 2) مطابقة ضبابية (≥ 88%) — rapidfuzz على كامل الكتالوج، لا قصّ 500.
                    #    القصّ السابق (our_names_list[:500]) كان يترك تكرارات ما بعد الـ500
                    #    تُعرَض كـ«مفقود» ⇒ خطر إعادة إضافة منتج نملكه للمتجر الحيّ. extractOne
                    #    بمستوى C يمسح كامل القائمة بسرعة أعلى من difflib المقصوص.
                    _best = rf_process.extractOne(name, our_names_list, scorer=fuzz.ratio)
                    best_ratio = (_best[1] / 100.0) if _best else 0.0

                    if best_ratio >= 0.88:
                        plan.moves.append(ProductMove(
                            product_name=str(row.get(missing_name_col, "—")),
                            product_id=str(row.get("معرف_المنافس", row.get("معرف_المنتج", ""))),
                            old_section="missing", new_section="review",
                            ai_reason=f"تشابه عالي ({best_ratio:.0%}) مع منتج في كتالوجنا",
                            ai_confidence=best_ratio,
                            row_index=to_process.index[idx],
                        ))
                        dedup_count += 1

                except Exception as exc:
                    plan.errors.append(f"خطأ في فحص مفقود [{idx}]: {exc}")

                if progress_cb and idx % 20 == 0:
                    progress_cb("مفقودات", idx + 1, total)

            plan.missing_deduplicated = dedup_count
            plan.missing_confirmed = total - dedup_count - skipped_empty

        except Exception as exc:
            plan.errors.append(f"فشل مراجعة المفقودات: {exc}")
            plan.missing_confirmed = total

        if progress_cb:
            progress_cb("مفقودات", total, total)

