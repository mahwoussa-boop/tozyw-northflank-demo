# Playbook: Verify a programmer's report / code claim

Use this when the owner pastes a diagnosis, a refactor plan, a "dead code" list, or a "✅ fixed in commit X" message and (explicitly or implicitly) wants you to check it and say what's safe to send back.

## The core method: prove each claim against the real code

You have read access to the repo (`C:\Users\Hp\Desktop\مهووس-للتوزيع`). For **every** factual claim, get ground truth — do not assess plausibility.

1. **"Symbol X is dead / unused"** → `Grep` the symbol across the whole repo (`output_mode: content`, `-n: true`). Read every hit.
   - Hits only inside its own definition file (definition + self-reference) → genuinely dead. Safe to delete.
   - Any hit in `tests/` → **dead in production but used by tests.** Deleting it breaks the suite. This is the single most common false-positive. Flag: "update the test in the same commit, or keep with `# TEST-ONLY`."
   - Hits in `api/`, `__all__`, or via `getattr`/`importlib`/string dispatch → **not dead** (dynamic/exported use). grep-only analysis misses these — say so.

2. **"X is a duplicate of Y (unify them)"** → don't trust the name. `Read` both implementations and compare behavior.
   - If they differ (different normalization, timeout, defaults, side effects) → unifying **changes behavior** and is unsafe. Recommend dropping the unify step.
   - Example from this repo: `engines/engine.py:normalize` (synonym replacement + lowercase) vs `engines/mahally_scraper.py:normalize` (NFKC + diacritic strip, a `@staticmethod` used via `self.normalize()`). Same name, **different jobs** — must NOT be merged.

3. **"Fixed in commit X (line N now = ...)"** → `Read` line N. Confirm the literal change. Line numbers drift ±a few — grep the symbol if the line moved.

4. **"requirements.txt is missing dep Z"** → factually check (`Read` it), but also check **intent**: this repo deliberately comments out scraper deps (selenium/bs4/aiohttp) with a note that the core app boots without them. Adding them as hard deps contradicts the documented lightweight-core design. Distinguish "factually absent" from "should be added".

5. **A "fix" that depends on config/keys** → trace whether the enabling key/flag is actually set. A guard that blocks bad data is a no-op if the upstream fetch that produces the data is disabled (e.g., a notes-quality gate does nothing when no Gemini key means notes are never fetched). See `diagnose-pipeline.md`.

## Catalogue of false-positives to actively hunt
- **Test-only usage** marked as dead (run_analysis, AutoPilotReport, kpi_metrics, processed_rows were all "dead" but tested).
- **Behaviorally-different "duplicates"** (the two `normalize`s; two `fetch_og_image_url` with 12s vs 6s timeouts).
- **Hardcoded fallbacks** presented as fixed — e.g. a classifier whose final `else` still returns a wrong default. Read the actual return line, not the function's docstring.
- **Over-caution in a prior "verification"** — the owner's other helper has wrongly claimed an import was "used" when grep showed it only at the import line. Verify the verifier too.
- **Config-gated features** that look done in code but never run in production (a default-False flag, a missing key).

## Verdict format
Give the owner a per-claim verdict they can trust: **✅ correct** (with the grep evidence) / **⚠️ partially correct** (true but with a caveat that affects safety) / **❌ wrong** (with the contradicting line). Lead with the bottom line. If something is unsafe to do, say so plainly and explain why in terms of what would break.

## The harm-proof message template for the programmer

When the owner asks "ماذا أرسل للمبرمج؟", give them a copy-paste block. Adapt this skeleton — it encodes the safety the owner can't judge for themselves:

```
نفّذ التغيير بأمان — الإنتاج حيّ، لا كسر مسموح.

قواعد إلزامية:
1. Git: فرع جديد + commit لكل خطوة منفصلة + وسم الحالة السليمة قبل البدء (للتراجع).
2. بوابة الاختبارات: شغّل `pytest -q` قبل البدء وبعد كل خطوة. نقَص العدد أو فشل ⇒ تراجَع، لا تكمل.
3. قبل حذف أي رمز: grep في كامل المستودع شاملاً tests/ + افحص getattr/importlib/__all__/api.
   مستخدَم في اختبار ⇒ حدّث الاختبار في نفس الـcommit أو أبقِه مع # TEST-ONLY. احذف بالاسم لا برقم السطر.
4. لا تلمس #PRESERVED_LOGIC؛ لا تغيّر سلوك normalize/المطابقة/التسعير/التصدير.
5. كل خطوة تنتهي بـ `pytest -q` أخضر + commit.

[ثم: التصحيحات المحدّدة التي وجدتَها بالدليل — خطوة تُحذف، خطوة تُليَّن، إلخ.]

⛔ أجّل تقسيم الملفات الكبيرة (engine.py / db_manager.py / ai_engine.py): الأخطر —
   تبعيات دائرية + db_manager ينفّذ آثاراً عند الاستيراد. اعزله خلف تغطية اختبارات كاملة، دالة-دالة مع re-export.
ابدأ بالخطوة الأولى فقط وأرِني pytest قبل/بعد، ثم توقّف لموافقتي.
```

The point of the template: the owner cannot evaluate code safety, so your message must make breakage hard regardless of how the programmer executes — git revert path, test gate, grep-before-delete, and an explicit "defer the risky refactor" line. Always tailor the middle section to the specific evidence you found.
