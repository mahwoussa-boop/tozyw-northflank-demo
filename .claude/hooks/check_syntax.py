#!/usr/bin/env python
"""مهووس — فحص نحوي سريع بعد كل تعديل ملف (Claude Code PostToolUse hook).

يقرأ JSON الأداة من stdin، وإن كان الملف المعدَّل ‎.py يشغّل py_compile عليه.
عند وجود خطأ نحوي: يُرجع رمز الخروج 2 فيصل الخطأ فوراً إلى كلود ليُصلحه.
لا يفحص الاستيراد/المنطق — تلك مهمّة حزمة الاختبارات وبوّابة pre-commit.

⚠️ لا تلفّ أمر هذا الخطّاف بـ``cmd /c`` في ``.claude/settings.local.json``.
مُثبَت بالمسبار (2026-07-25): مع الغلاف يمرّ **محتوى الملف المعدَّل** عبر صَدَفة
تفسّر ``>`` كإعادة توجيه، فينشأ ملف صفريّ في جذر المستودع اسمه الكلمة التالية
للرمز (``{v:>7ZZZBETA}`` ⇒ ملف ``7ZZZBETA}``). ستة ملفات كهذه تراكمت في الجذر،
والخطر الحقيقي أن محتوى يحوي ``> app.py`` كان ليقتطع ملفاً حيّاً. الصيغة الآمنة:
``.venv/Scripts/python.exe .claude/hooks/check_syntax.py`` بلا غلاف.
"""
import sys
import os
import json
import subprocess

# رسالة الخطأ عربية، وstderr على ويندوز يفتح بترميز النظام فتصل مشوّهة لكلود.
try:
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)  # لا مدخل صالح ⇒ لا تعترض

tool_input = data.get("tool_input") or {}
path = tool_input.get("file_path") or ""

if not path.endswith(".py") or not os.path.exists(path):
    sys.exit(0)

# فضّل بايثون الـvenv؛ وإلا استخدم الحالي
py = os.path.join(".venv", "Scripts", "python.exe")
if not os.path.exists(py):
    py = sys.executable

result = subprocess.run(
    [py, "-m", "py_compile", path],
    capture_output=True,
    text=True,
)

if result.returncode != 0:
    sys.stderr.write("⛔ خطأ نحوي في %s:\n%s" % (path, result.stderr))
    sys.exit(2)  # 2 = اعتراض: أعِد الخطأ إلى كلود

sys.exit(0)
