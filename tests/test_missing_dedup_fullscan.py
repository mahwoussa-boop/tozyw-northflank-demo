# -*- coding: utf-8 -*-
"""انحدار (v37): كشف تكرار المفقودات يمسح كامل الكتالوج لا أول 500 صنف فقط.

خلفية العطب: `AIRedistributor._process_missing` كان يقارن كل اسم مفقود ضبابياً
بأول 500 صنف فقط من كتالوجنا (`our_names_list[:500]`). فإن تجاوز كتالوجنا 500 صنف،
ينجو التكرار الضبابي الواقع بعد الـ500 فيُعرَض كـ«مفقود» → خطر إعادة إضافة منتج
نملكه للمتجر الحيّ. الإصلاح استبدل الحلقة المقصوصة بـ rapidfuzz.extractOne على
كامل القائمة. هذا الاختبار يضع المنتج المملوك عند الفهرس 600 (بعد السقف القديم)
ويثبت أنه يُكتشَف الآن.
"""
import pandas as pd

from services.ai_redistributor import AIRedistributor


class _StubRepo:
    def log_decision(self, decision):
        pass

    @staticmethod
    def new_batch_id():
        return "test-batch"


def _make_redistributor():
    r = AIRedistributor.__new__(AIRedistributor)
    r._ai = None
    r._repo = _StubRepo()
    r._threshold = 0.85
    r._batch_size = 20
    r._max_per_section = 200
    r._router = None
    return r


def test_hidden_duplicate_beyond_500_is_caught():
    r = _make_redistributor()
    owned = "شانيل بلو دو شانيل او دو بارفان 100 مل"
    # 600 اسم حشو ثم المنتج المملوك عند الفهرس 600 — خارج سقف الـ500 القديم.
    names = [f"منتج حشو رقم {i}" for i in range(600)] + [owned]
    our_catalog = pd.DataFrame({"المنتج": names})
    # اسم المفقود = تكرار ضبابي (فرق مسافة، ليس تطابقاً حرفياً) للمنتج المملوك.
    missing = pd.DataFrame({
        "منتج_المنافس": ["شانيل بلو دو شانيل او دو بارفان 100مل"],
        "معرف_المنافس": ["dup-1"],
    })

    plan = r.build_plan({}, missing_df=missing, our_catalog=our_catalog)

    assert plan.missing_deduplicated == 1, (
        "التكرار الواقع بعد الفهرس 500 لم يُكتشَف — خطر إعادة إضافة منتج مملوك"
    )
    assert plan.missing_confirmed == 0
    review_moves = [m for m in plan.moves if m.new_section == "review"]
    assert any("تشابه" in m.ai_reason for m in review_moves), \
        "لم تُسجَّل حركة تكرار ضبابي إلى المراجعة"


def test_unique_missing_still_reported_missing():
    # ضمان عدم الإفراط: منتج مفقود حقيقي لا نملكه يبقى «مؤكد مفقود».
    r = _make_redistributor()
    our_catalog = pd.DataFrame({"المنتج": [f"منتج حشو رقم {i}" for i in range(600)]})
    missing = pd.DataFrame({
        "منتج_المنافس": ["عطر نادر جداً لا نملكه إطلاقاً 75 مل"],
        "معرف_المنافس": ["uniq-1"],
    })
    plan = r.build_plan({}, missing_df=missing, our_catalog=our_catalog)
    assert plan.missing_deduplicated == 0
    assert plan.missing_confirmed == 1
