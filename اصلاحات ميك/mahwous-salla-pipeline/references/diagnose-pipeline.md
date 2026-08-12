# Playbook: Diagnose wrong category / brand / description / missing specs

The owner says "كل المنتجات تصل بتصنيف عطور رجالية" or "الوصف خطأ" or "المنتجات لا تصل". Resist the first guess. Trace the **actual value** through three layers and find which one is responsible.

## The three layers (and how to tell them apart)
1. **App** — what literal string the Python app puts in the webhook field (`اسم التصنيف`, `الوصف`, `الماركة`).
2. **Make** — whether the scenario's expression transforms/mangles it (see `make-scenario.md`).
3. **Salla** — what it does with an empty/unmatched value (may apply a store default).

Decisive test for "wrong category": the Make category expression does **exact match** and returns the matched id or **empty** → it can never *invent* `عطور رجالية`. So if everything lands in `عطور رجالية`, either the app **sent** that string, or Salla **defaulted** to it on empty. Distinguish by checking what the app emits (below). If the app emits a coarse value like `🌸 عطور` (with emoji) it wouldn't match Salla at all → empty → Salla default — a different fix.

## Category decision in the app (where to look)
- The webhook builder reads an already-set field — `utils/make_helper.py:_category_name(p)` just reads `اسم التصنيف`/`تصنيف_المنتج`/… It does **not** classify. So the bug is upstream, in whatever set those keys.
- The live classifier is `services/category_classifier.py:classify_category(name, gender_hint, brand)`. It checks brand signals (`_CARE_BRANDS`, `_MAKEUP_BRANDS`), body/hair/skin/incense/candle/home/mist/accessory/kids/tester/niche/pheromone/dupe keywords — and then a **final fallback**. **Read the actual final `return` line.** A fallback of `else "عطور رجالية"` makes every genderless, non-specific perfume default to men's — the root of "all → عطور رجالية". The owner's chosen policy is `else "العطور"` (neutral parent, which is a real Salla category id 1319068713 and resolves in Make).
- Watch for **other** classifiers that also default to men's: `engines/ai_engine.py:auto_infer_category` (a fallback path) and `engines/engine.py:classify_product_category` (returns coarse emoji buckets — different output entirely). Confirm which one actually feeds the send.

## Gender detection (why "pass the gender" doesn't fix it alone)
Both `services/category_classifier.py:_gender` and `engines/engine.py:extract_gender` are **keyword-only** — they return a gender only if the name literally contains `رجالي/men/homme` or `نسائي/women/femme`. Most perfume names ("ديور سوفاج 100مل") have no such word → gender `""` → the classifier's genderless fallback fires. So:
- Passing `extract_gender`'s output into the classifier does **not** help — it fails on the same names.
- `fetch_fragrantica_info` does **not** return a gender field. There is no smart gender source wired in. Therefore the correct fix is the **neutral fallback** (`العطور`), not "detect gender better", unless someone adds a real gender source (a brand→gender map, or the dead `gender_hint` path).

## Description & specs (real source vs hallucination)
- The only notes/specs fetch in the live path is `engines/ai_engine.py:fetch_fragrantica_info`. It does **not scrape Fragrantica** — it calls **Gemini with Google grounding** (`_call_gemini(prompt, grounding=True)`), with an **ungrounded fallback** (line ~782) that can invent notes from model memory.
- **Critical config dependency:** `fetch_fragrantica_info` uses **Gemini only** (no OpenRouter fallback), and `_call_gemini` returns `None` immediately if `GEMINI_API_KEYS` is empty (`config.py`). So **with no Gemini key, notes are never fetched** — every description shows "غير متوفر" for the pyramid. An **OpenRouter key does not help here** (it only powers description *writing* and matching, not notes *fetching*). Keys live in `.env` / `.streamlit/secrets.toml` as `GEMINI_API_KEY` / `OPENROUTER_API_KEY`. **Check whether the needed key is actually set** before concluding anything about specs.
- The description builder (`services/missing_orchestrator.py:_ensure_golden_description` → `utils/salla_shamel_export.py:generate_salla_html_description`, and the `MAHWOUS_SALLA_PROMPT`) is **well-guarded against fabrication**: it uses only the fetched notes and writes "غير متوفر" for anything missing; the AI prompt forbids inventing notes. So the description won't fabricate — but it's only as rich as the (Gemini-dependent) fetch.

## Enrichment gating (why some sends have no specs)
- Per-page flag `enrich_with_ai`: some UI paths pass **`False`** (fast, no notes) and some `True`. A "fast" send path produces spec-less products. Grep `enrich_with_ai=` to see which buttons enrich.
- `services/missing_orchestrator.py` merges Fragrantica data **only on success** (`if frag_data.get("success")`), so on failure the notes keys are simply **absent**, not `"غير متوفر"`.

## Quality gates (and the no-op trap)
- The **real** export gate is `utils/product_gate.py:validate_and_enrich` (called from `make_helper.py`), **not** `services/enrichment_service.py:validate_for_make` (that one isn't on the live send path — verify before trusting it).
- `utils/product_gate.py:assess_notes_quality` blocks a perfume whose notes are all "غير متوفر" or flagged ungrounded, sending it to **review instead of silent publish**. But it is **fail-open when the notes fields are absent** — so with no Gemini key (fields never set) it does **nothing**, and spec-less perfumes ship anyway. A guard only protects once the upstream fetch that fills its fields is actually enabled. Always check that interaction.

## Diagnosis checklist
1. Grep the suspected default string (e.g. `عطور رجالية`) — find every hardcoded fallback.
2. Find which function sets the webhook field, and `Read` its real `return`/fallback lines (not docstrings).
3. Decide app vs Make vs Salla by what literal string is actually emitted (exact-match Make can't invent a category).
4. For specs/description: confirm the enabling key (`GEMINI_API_KEY`) and the enrich path/flag are actually on; trace whether the guard is active or fail-open.
5. Report the layer, the exact line, and a fix — and if it's app-side, hand the owner the safe programmer message (see `verify-programmer-code.md`).
