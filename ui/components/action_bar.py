"""ui/components/action_bar.py — شريط الإجراءات الجماعية (منطق خالص + fragment).

نطاقان واضحان:
- **نطاق الصفحة:** حذف ناعم لصفوف الصفحة المعروضة فقط.
- **نطاق القسم:** إرسال القسم كامله لـ Make (دفعات 20 + إعادة محاولة + تراجع
  أُسّي + تقدّم حيّ — نقل دلالة ``make_helper.send_batch_smart``) + تصدير
  Excel/CSV للقسم كامله عند الطلب.

``chunk_payload`` و``send_in_batches`` خالصتان قابلتان للاختبار (الخدمة والنوم
محقونان ⇒ لا شبكة/انتظار حقيقي). الإرسال/التصدير عبر ``ExportService`` كما هو.
#PRESERVED_LOGIC: make_helper.send_batch_smart (batch_size=20/max_retries=3/backoff 2×n/sleep 0.5).
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import pandas as pd

from core.enums import ActionType
from services.decision_journal import record_decision
from services.export_service import ExportService, to_csv

# خريطة المفتاح → نوع القسم لحمولة Make (#PRESERVED_LOGIC _section_price).
_SECTION_TYPE: dict[str, str] = {
    "price_raise": "raise", "price_lower": "lower", "approved": "approved",
    "review": "update", "excluded": "update", "missing": "missing",
}
_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_BATCH_SIZE = 20    # حجم دفعة Make (#PRESERVED_LOGIC send_batch_smart)
_MAX_RETRIES = 3    # محاولات لكل دفعة قبل اعتبارها فاشلة


@dataclass(frozen=True)
class BulkAction:
    """نتيجة شريط الإجراءات: الإجراء المختار + المفاتيح المستهدفة."""

    action: Optional[ActionType] = None
    keys: list[str] = field(default_factory=list)


def page_keys(view_df: pd.DataFrame, key_col: str) -> list[str]:
    """مفاتيح صفوف الصفحة الحالية (خالص)."""
    if not isinstance(view_df, pd.DataFrame) or view_df.empty \
            or key_col not in view_df.columns:
        return []
    return [str(v).strip() for v in view_df[key_col].tolist() if str(v).strip()]


def chunk_payload(products: list, size: int) -> list[list]:
    """يقسّم الحمولة إلى دفعات بحجم ``size`` (خالص). ``size<=0`` ⇒ دفعة واحدة."""
    if not products:
        return []
    if size <= 0:
        return [list(products)]
    return [products[i:i + size] for i in range(0, len(products), size)]


def send_in_batches(
    service: Any,
    url: str,
    products: list,
    *,
    batch_size: int = _BATCH_SIZE,
    max_retries: int = _MAX_RETRIES,
    sleep: Callable[[float], Any] = time.sleep,
    progress_cb: Optional[Callable[[int, int], Any]] = None,
    confirmed_items_cb: Optional[Callable[[list], Any]] = None,
    envelope: str = "products",
) -> dict[str, Any]:
    """يرسل الحمولة على دفعات مع إعادة محاولة وتراجع أُسّي (نقل send_batch_smart).

    خالص قابل للاختبار: ``service`` (له ``post_to_make``) و``sleep`` محقونان
    ⇒ لا شبكة/انتظار حقيقي. يجمّع المُرسَل/الفاشل ولا ينهار على استثناء دفعة.
    ``envelope`` يُمرَّر لـ ``post_to_make`` (data للمنتجات الجديدة). ``errors`` تحمل
    آخر سبب رفض حقيقي لكل دفعة فاشلة (نص رد Make) لعرضه للمستخدم بدل الصمت.
    """
    chunks = chunk_payload(products, batch_size)
    total = len(products)
    sent = accepted = failed = 0
    errors: list[str] = []
    blocked_low_price: list = []  # أ2: عناصر حجزها send_floor_guard — لا فشل ولا إرسال
    held_outside_band: list = []  # أ4: عناصر حجزها send_band_guard (خارج نطاق v1) — لا فشل ولا إرسال
    held_low_quality: list = []   # م4: عناصر حجزها send_quality_guard (ثقة/توفّر/حداثة)
    for index, chunk in enumerate(chunks):
        ok = pending_confirmation = False
        last_err = ""
        res: dict[str, Any] = {}
        for attempt in range(1, max_retries + 1):
            try:
                res = service.post_to_make(url, chunk, envelope=envelope)
                ok = bool(res.get("confirmed", res.get("success")))
                pending_confirmation = res.get("state") == "accepted"
                if not ok:
                    last_err = str(
                        res.get("body") or res.get("error")
                        or res.get("status_code") or "")
            except Exception as exc:
                ok = False
                last_err = str(exc)
                res = {}
            if ok or pending_confirmation or res.get("state") in {
                "blocked_low_price", "held_outside_band", "held_low_quality",
            }:
                break
            if attempt < max_retries:
                sleep(2 * attempt)
        _blocked = res.get("blocked_low_price", []) or []
        _held = res.get("held_outside_band", []) or []
        _held_q = res.get("held_low_quality", []) or []
        blocked_low_price.extend(_blocked)
        held_outside_band.extend(_held)
        held_low_quality.extend(_held_q)
        eligible = len(chunk) - len(_blocked) - len(_held) - len(_held_q)
        sent += eligible if ok else 0
        accepted += eligible if pending_confirmation else 0
        if ok and confirmed_items_cb:
            # The ExportService provides this field only after Make/Salla
            # confirmation.  Do not infer it from the HTTP response or from
            # the original chunk: guards may have held some rows.
            confirmed_items = res.get("delivered_items", [])
            if isinstance(confirmed_items, list):
                confirmed_items_cb(confirmed_items)
        if not ok and not pending_confirmation and not (_blocked or _held or _held_q):
            failed += len(chunk)
        if not ok and not pending_confirmation and last_err:
            errors.append(last_err)
        if progress_cb:
            progress_cb(
                sent + accepted + failed + len(blocked_low_price) + len(held_outside_band)
                + len(held_low_quality),
                total,
            )
        if index < len(chunks) - 1:
            sleep(0.5)
    return {
        "success": sent > 0, "sent": sent, "accepted": accepted, "failed": failed,
        "total": total, "errors": errors[:5],
        "blocked_low_price": blocked_low_price,
        "held_outside_band": held_outside_band,
        "held_low_quality": held_low_quality,
    }


def _delivery_identifiers(items: list[dict[str, Any]]) -> tuple[set[str], set[str]]:
    """Return normalized Salla identifiers and names from confirmed payload items."""
    skus: set[str] = set()
    names: set[str] = set()
    for item in items:
        sku = str(item.get("NO") or item.get("product_id") or "").strip()
        name = str(item.get("name") or item.get("اسم المنتج") or "").strip()
        if sku:
            skus.add(sku)
        if name:
            names.add(name)
    return skus, names


def _webhook_env(key: str) -> str:
    """متغيّر بيئة الـwebhook حسب القسم (المفقود=منتجات جديدة، الباقي=تحديث أسعار)."""
    return "WEBHOOK_NEW_PRODUCTS" if key == "missing" else "WEBHOOK_UPDATE_PRICES"


def _excel_bytes(df: pd.DataFrame) -> Optional[bytes]:
    """يولّد بايتات Excel عبر ``ExportService.to_excel`` (None عند تعذّر المحرّك)."""
    import tempfile

    handle = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
    handle.close()
    try:
        ExportService.to_excel(df, handle.name)
        with open(handle.name, "rb") as fh:
            return fh.read()
    except Exception:
        return None
    finally:
        try:
            os.unlink(handle.name)
        except OSError:
            pass


def _prepare_download(st: Any, df: pd.DataFrame, key: str, fmt: str) -> None:
    """يجهّز ملف التنزيل (CSV فوري/Excel عند الطلب) ويخزّنه في الحالة."""
    if fmt == "csv":
        st.session_state[f"{key}_dl"] = (
            f"{key}.csv", to_csv(df).encode("utf-8-sig"), "text/csv",
        )
        return
    data = _excel_bytes(df)
    if data is None:
        st.warning("⚠️ تعذّر توليد Excel (openpyxl غير متاح)")
        return
    st.session_state[f"{key}_dl"] = (f"{key}.xlsx", data, _XLSX_MIME)


def _send_section_to_make(st: Any, df: pd.DataFrame, key: str) -> None:
    """يرسل القسم كامله لـ Make على دفعات مع شريط تقدّم حيّ ويعلّم المنتجات كمُعالَجة."""
    url = os.environ.get(_webhook_env(key), "")
    if not url:
        st.error("⚠️ رابط Webhook غير مهيّأ (.env)")
        return
    service = ExportService()
    # المفقودات = منتجات جديدة (مظروف data + مفاتيح سلة العربية)؛ الباقي = تحديث أسعار.
    if key == "missing":
        products = service.new_products_payload(df)
        envelope = "data"
        # ── بوابة منع التكرار بالبصمة (catalog ← sent) قبل الإرسال ──
        try:
            from utils.missing_queue_manager import screen_for_duplicates
            _state = st.session_state.get("_app_state_v2")
            _cat = getattr(_state, "our_catalog", None) if _state else None
            _screen = screen_for_duplicates(products, _cat)
            products = _screen.kept
            _s = _screen.summary
            if _s.get("match") or _s.get("likely"):
                st.info(
                    f"🛡️ منع التكرار: {_s.get('match',0)} مكرر مُسقَط · "
                    f"{_s.get('likely',0)} محتمل محجوز للمراجعة · {_s.get('new',0)} جديد"
                )
        except Exception:
            pass  # fail-open — لا نعيق الإرسال على خطأ البوابة
    else:
        products = service.make_payload(df, _SECTION_TYPE.get(key, "update"))
        envelope = "products"
    if not products:
        st.warning("لا صفوف صالحة للإرسال — أو كلها مكرّرة موجودة في متجرك")
        return
    bar = st.progress(0.0, text=f"جاري الإرسال (0/{len(products)})…")

    def _on_progress(done: int, total: int) -> None:
        bar.progress(
            min(done / total, 1.0) if total else 1.0,
            text=f"جاري الإرسال ({done}/{total})…",
        )

    confirmed_items: list[dict[str, Any]] = []
    result = send_in_batches(
        service, url, products, progress_cb=_on_progress,
        confirmed_items_cb=confirmed_items.extend, envelope=envelope,
    )
    bar.progress(1.0, text="اكتمل ✓")
    if result["sent"]:
        note = f"✅ أُرسل {result['sent']} منتجاً"
        if result["failed"]:
            note += f" · ❌ فشل {result['failed']}"
        st.success(note)

        # ── تعليم المنتجات كمُعالَجة بعد التأكيد فقط ──
        from conf.constants import COL_COMP_LINK, COL_COMP_NAME, COL_OUR_NAME, COL_OUR_ID
        state = st.session_state.get("_app_state_v2")
        if state:
            confirmed_skus, confirmed_names = _delivery_identifiers(confirmed_items)
            for _, row in df.iterrows():
                try:
                    getter = row.get if hasattr(row, "get") else (lambda k, d=None: d)
                    link = str(getter(COL_COMP_LINK, "") or "").strip()
                    name = str(
                        getter(COL_COMP_NAME, "") or getter(COL_OUR_NAME, "")
                        or getter("أسم المنتج", "") or getter("المنتج", "")
                    ).strip()
                    sku = str(getter(COL_OUR_ID, "") or getter("sku", "") or "").strip()
                    price = 0.0
                    try:
                        price = float(getter("سعر_المنافس", 0) or getter("السعر", 0) or 0)
                    except (TypeError, ValueError):
                        pass

                    is_confirmed = (
                        key == "missing" and name in confirmed_names
                    ) or (
                        key != "missing" and sku in confirmed_skus
                    )
                    if not is_confirmed:
                        continue

                    if key == "missing" and link:
                        state.mark_missing_processed(link)
                    elif sku:
                        state.mark_price_processed(sku)

                    state.log_action(
                        key=link or sku or name,
                        name=name,
                        action=f"إرسال جماعي ({key})",
                        detail=f"بسعر {price:,.0f} ر.س ← Make",
                        kind="missing" if key == "missing" else "price",
                    )
                except Exception:
                    pass  # لا انهيار على صف فاسد
            state.persist_results()
    if result.get("accepted"):
        st.warning(
            f"📨 استلم Make {result['accepted']} منتجاً، لكن لم يصل تأكيد Salla النهائي؛ "
            "لم نعد إرسالها ولم نخفِها من المراجعة."
        )
    if not result["sent"] and result["failed"]:
        st.error(f"❌ فشل الإرسال ({result['failed']} منتجاً)")
    # أ2: عدّاد ما حجزه send_floor_guard (سعر < 50% من وسيط السوق) — لا فشل ولا صمت.
    _blocked = result.get("blocked_low_price") or []
    if _blocked:
        st.warning(
            f"🛡️ حُجز {len(_blocked)} منتجاً عن الإرسال — سعره أقل من 50% من وسيط "
            "السوق (احتمال خطأ كشط) — راجعه يدوياً قبل الإرسال."
        )
    # أ4: عدّاد ما حجزه send_band_guard لأقسام ENFORCED_SECTIONS (خارج ±20% من وسيط السوق) — لا فشل ولا صمت.
    _held = result.get("held_outside_band") or []
    if _held:
        st.warning(
            f"🧭 حُجز {len(_held)} منتجاً عن الإرسال — سعره خارج نطاق سياج v1 "
            "(80%-120% من وسيط السوق) — يحتاج تجاوزاً مسجَّلاً أو مراجعة يدوية."
        )
    # م4: ما حجزته بوّابة الأهلية (ثقة/توفّر/حداثة) — طابور مستقل **قابل للمراجعة**
    # لا مجرّد عدّاد: الحجب بلا رؤية يتحوّل إلى فقدان منتجات بصمت.
    _held_q = result.get("held_low_quality") or []
    if _held_q:
        st.warning(
            f"🔎 حُجز {len(_held_q)} منتجاً عن الإرسال — الدليل الذي بُني عليه القرار "
            "ضعيف (ثقة مطابقة منخفضة، أو كل عروض المنافسين نافدة، أو بيانات قديمة)."
        )
        with st.expander(f"عرض الـ{len(_held_q)} منتجاً المحجوزة وأسبابها", expanded=False):
            st.dataframe(
                pd.DataFrame([{
                    "المنتج": str(r.get("name") or r.get("comp_name") or ""),
                    "السعر": r.get("price"),
                    "ثقة المطابقة": r.get("match_score"),
                    "سبب الحجز": str(r.get("blocked_reason") or ""),
                } for r in _held_q if isinstance(r, dict)]),
                use_container_width=True, hide_index=True,
            )
    # إظهار سبب الرفض الحقيقي من Make (response.text) بدل الصمت.
    if result.get("errors"):
        st.error("سبب الرفض من Make: " + " | ".join(result["errors"]))


def render_action_bar(
    view_df: pd.DataFrame,
    key_col: str,
    key: str,
    *,
    section_df: Optional[pd.DataFrame] = None,
) -> BulkAction:
    """يعرض إجراءات الصفحة (حذف ناعم) + إجراءات القسم (إرسال Make + تصدير).

    ``section_df`` = القسم المفلتر كامله (الإرسال/التصدير عليه)؛ غيابه ⇒ الصفحة.
    """
    import streamlit as st

    section = section_df if isinstance(section_df, pd.DataFrame) else view_df
    n_section = len(section) if isinstance(section, pd.DataFrame) else 0
    has_section = n_section > 0

    @st.fragment
    def _bar() -> BulkAction:
        page = page_keys(view_df, key_col)
        sel = st.checkbox(f"تحديد كل الصفحة ({len(page)})", key=f"{key}_all")
        
        # ── تخطيط محسّن: أزرار أكبر وأوضح ──
        c_del, c_xlsx, c_csv = st.columns(3)
        if c_del.button("🗑️ حذف ناعم", key=f"{key}_del", disabled=not sel,
                         use_container_width=True):
            st.session_state[f"{key}_result"] = BulkAction(ActionType.HIDE, page)
            # تسجيل صامت: حذف ناعم جماعي — صف لكل منتج محدّد (دفتر القرارات)
            for _nm in page:
                record_decision("ignore", str(_nm), key, extra={"bulk": True})
        if c_xlsx.button(f"📊 Excel ({n_section})", key=f"{key}_xlsxbtn",
                         disabled=not has_section, use_container_width=True):
            _prepare_download(st, section, key, "xlsx")
        if c_csv.button(f"📄 CSV ({n_section})", key=f"{key}_csvbtn",
                        disabled=not has_section, use_container_width=True):
            _prepare_download(st, section, key, "csv")
        ready = st.session_state.get(f"{key}_dl")
        if ready:
            fname, data, mime = ready
            st.download_button("📥 تنزيل الملف الجاهز", data, fname, mime=mime,
                               key=f"{key}_dl_btn", use_container_width=True)

        # ── فحص الماركات قبل الإرسال (للمفقودات فقط) ──
        brands_ok = True
        if key == "missing" and has_section:
            brands_ok = _render_brand_check(st, section, key)

        # ── إرسال Make: تأكيد + زر بارز ──
        confirm = st.checkbox(
            f"تأكيد إرسال القسم لـ Make ({n_section})",
            key=f"{key}_confirm", disabled=not (has_section and brands_ok),
            help="فعّل التأكيد ثم اضغط الإرسال (يمنع الإرسال الجماعي بالخطأ).",
        )
        if st.button("📤 إرسال القسم لـ Make.com", key=f"{key}_make",
                     disabled=not (has_section and confirm and brands_ok),
                     type="primary", use_container_width=True):
            _send_section_to_make(st, section, key)
        
        return _read_result(key)

    return _bar()


_brand_check_cache: Any = None  # كاش كسول (هويّة ثابتة عبر التشغيلات)


def _section_fingerprint(df: Optional[pd.DataFrame]) -> tuple:
    """بصمة رخيصة للقسم: (عدد الصفوف، أسماء الأعمدة). ثابتة عبر إعادات التشغيل."""
    if df is None:
        return (0, ())
    return (int(len(df)), tuple(str(c) for c in df.columns))


def _get_brand_check_cache(st: Any) -> Any:
    """نسخة ``check_and_prepare_brands`` مُخزَّنة بمفتاح بصمة القسم — لا تُعاد حلقة O(n)
    كل تفاعل. الـ``_df`` (بسابقة ``_``) لا يُهاش؛ الحساب مرّة واحدة لكل بصمة قسم.
    """
    global _brand_check_cache
    if _brand_check_cache is None:

        @st.cache_data(show_spinner=False)
        def _compute(fingerprint: tuple, _df: pd.DataFrame) -> tuple:
            from services.brand_manager import check_and_prepare_brands
            return check_and_prepare_brands(_df.to_dict("records"))

        _brand_check_cache = _compute
    return _brand_check_cache


def _render_brand_check(st: Any, df: pd.DataFrame, key: str) -> bool:
    """يفحص الماركات المفقودة ويعرض واجهة تنزيل ملف + تأكيد تحميل.

    يُرجع True إذا كل الماركات متوفرة أو تم تأكيد التحميل.
    """
    # حالة: المستخدم أكّد مسبقاً أنه حمّل الماركات
    if st.session_state.get(f"{key}_brands_uploaded"):
        st.success("✅ تم تأكيد تحميل الماركات — جاهز للإرسال")
        return True

    try:
        from services.brand_manager import generate_brands_excel, add_to_known_brands

        # كاش بمفتاح بصمة القسم — لا تُعاد حلقة الفحص O(n) كل تفاعل.
        brand_rows, missing_names = _get_brand_check_cache(st)(
            _section_fingerprint(df), df
        )

        if not missing_names:
            return True  # كل الماركات موجودة

        # ── ماركات مفقودة! (مطويّ افتراضياً + تصدير CSV — طيّ لا حذف) ──
        with st.expander(f"⚠️ {len(missing_names)} ماركة غير موجودة", expanded=False):
            st.caption("يجب إضافتها في سلة قبل إرسال المنتجات:")
            st.write("، ".join(missing_names))
            st.download_button(
                "📥 تحميل CSV",
                data=to_csv(pd.DataFrame({"الماركة": missing_names})).encode("utf-8-sig"),
                file_name=f"ماركات_ناقصة_{len(missing_names)}.csv",
                mime="text/csv",
                key=f"{key}_brands_csv",
                use_container_width=True,
            )

        c1, c2 = st.columns(2)
        with c1:
            # زر تنزيل ملف الماركات
            try:
                excel_data = generate_brands_excel(brand_rows)
                st.download_button(
                    f"📥 تنزيل ملف الماركات ({len(missing_names)} ماركة)",
                    data=excel_data,
                    file_name=f"ماركات_جديدة_{len(missing_names)}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    key=f"{key}_brands_dl",
                    use_container_width=True,
                )
            except Exception as exc:
                st.error(f"❌ فشل إنشاء ملف الماركات: {exc}")

        with c2:
            # زر "تم التحميل"
            if st.button(
                "✅ تم تحميل الماركات للمتجر",
                key=f"{key}_brands_confirm",
                type="primary",
                use_container_width=True,
            ):
                add_to_known_brands(missing_names)
                st.session_state[f"{key}_brands_uploaded"] = True
                st.rerun()

        st.caption(
            "💡 حمّل الملف → افتح لوحة تحكم سلة → ماركات → استيراد → "
            "ارفع الملف → عُد هنا واضغط «✅ تم تحميل الماركات»"
        )
        return False

    except ImportError:
        return True  # الخدمة غير متاحة — لا نعيق الإرسال
    except Exception as exc:
        st.caption(f"⚠️ تعذّر فحص الماركات: {exc}")
        return True  # لا نعيق الإرسال عند خطأ


def _read_result(key: str) -> BulkAction:
    """يقرأ نتيجة الإجراء من الحالة (يُستهلك مرة)."""
    import streamlit as st

    result = st.session_state.pop(f"{key}_result", None)
    return result if isinstance(result, BulkAction) else BulkAction()
