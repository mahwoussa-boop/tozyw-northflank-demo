"""e2e_health_check.py — End-to-End Health Check + AI Mini Run (20-50 products).

Verifies:
  1. AI Router connectivity and configuration
  2. AI Mini Run on 30 review products (safe sample)
  3. Closed-loop accounting (0% leakage)
  4. UI link integrity (target="_blank" HTML)
  5. Make send button code integrity (try/except + NO field + hide + rerun)
"""
from __future__ import annotations

import ast
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# Force UTF-8 output
os.environ["PYTHONUTF8"] = "1"


def _header(title: str) -> None:
    print(f"\n{'=' * 70}")
    print(f"  {title}")
    print(f"{'=' * 70}")


def _pass(msg: str) -> None:
    print(f"  [PASS] {msg}")


def _fail(msg: str) -> None:
    print(f"  [FAIL] {msg}")


def _info(msg: str) -> None:
    print(f"  [INFO] {msg}")


def _warn(msg: str) -> None:
    print(f"  [WARN] {msg}")


# ============================================================================
#  CHECK 1: AI Configuration
# ============================================================================
def check_ai_config() -> bool:
    _header("CHECK 1: AI Configuration")
    from conf.settings import Settings
    settings = Settings.load()

    has_openrouter = bool(settings.openrouter_api_key.strip())
    has_gemini = bool(settings.gemini_api_keys)
    has_cohere = bool(settings.cohere_api_key.strip())
    any_ai = settings.any_ai_configured

    _info(f"OpenRouter: {'YES' if has_openrouter else 'NO'}")
    _info(f"Gemini:     {'YES (' + str(len(settings.gemini_api_keys)) + ' keys)' if has_gemini else 'NO'}")
    _info(f"Cohere:     {'YES' if has_cohere else 'NO'}")

    if any_ai:
        _pass("At least one AI provider is configured")
    else:
        _fail("No AI provider configured! Set OPENROUTER_API_KEY or GEMINI_API_KEY in .env")
    return any_ai


# ============================================================================
#  CHECK 2: AI Router Service Integrity
# ============================================================================
def check_ai_router_service() -> bool:
    _header("CHECK 2: AI Router Service Integrity")
    try:
        from services.ai_router_service import AIRouterService, RouteResult
        from services.ai_service import AIService
        from services.ai_prompts import (
            ROUTING_SYSTEM_PROMPT,
            build_routing_prompt,
            parse_ai_routing_response,
        )
        _pass("All AI modules import successfully")

        # Test prompt builder with sample data
        sample = [{"our_name": "Test A", "comp_name": "Test B", "our_price": "100",
                    "comp_price": "95", "match_score": "72", "brand": "Brand", "decision": "review"}]
        prompt = build_routing_prompt(sample)
        assert len(prompt) > 50, "Prompt too short"
        _pass(f"Prompt builder works (length={len(prompt)} chars)")

        # Test parser with mock response
        mock_resp = '[{"index": 0, "match_type": "MATCH", "confidence": 0.92, "reason": "Same product"}]'
        parsed = parse_ai_routing_response(mock_resp)
        assert parsed is not None and len(parsed) == 1
        assert parsed[0]["match_type"] == "MATCH"
        _pass("Response parser works correctly")

        # Test RouteResult with empty DF
        import pandas as pd
        ai_svc = AIService(providers=[], settings=None)
        router = AIRouterService(ai_svc, confidence_threshold=0.85)
        result = router.route_review(pd.DataFrame())
        assert isinstance(result, RouteResult)
        _pass("Router handles empty DataFrame gracefully")

        return True
    except Exception as e:
        _fail(f"AI Router check failed: {e}")
        return False


# ============================================================================
#  CHECK 3: AI Mini Run (30 products from review section)
# ============================================================================
def check_ai_mini_run() -> dict:
    _header("CHECK 3: AI Mini Run (Safe Sample)")

    import json
    import pandas as pd
    from pathlib import Path
    from conf.constants import DATA_DIR

    # اللقطة صارت مجلّداً (صيغة 3) — نمرّ عبر جسر التوافق بدل فتح ملف مباشرةً.
    from ui.state_manager import load_snapshot_raw, snapshot_exists

    # Try loading from snapshot
    review_df = pd.DataFrame()
    if snapshot_exists():
        try:
            from ui.state_manager import _deserialize_snapshot
            snap = _deserialize_snapshot(load_snapshot_raw())
            sections = snap.get("sections", {})
            review_df = sections.get("review", pd.DataFrame())
            _info(f"Loaded review section: {len(review_df)} products")
        except Exception as e:
            _warn(f"Could not load snapshot: {e}")

    if review_df.empty:
        # Try building from cache
        _info("No review section in snapshot. Trying pricing cache...")
        try:
            from bootstrap import build_container
            from services.catalog_service import load_catalog
            import glob

            patterns = [
                os.path.join(os.path.expanduser("~"), "Downloads", "*", "mahwous_*_products.xlsx"),
                os.path.join(os.path.expanduser("~"), "Downloads", "mahwous_*_products.xlsx"),
            ]
            files = []
            for p in patterns:
                files.extend(glob.glob(p))
            files.sort(key=os.path.getmtime, reverse=True)

            if files:
                our_df = load_catalog(files[0])
                container = build_container()
                sections, result, missing, stats = (
                    __import__("bootstrap").run_pricing_analysis(container, our_df, use_ai=False)
                )
                review_df = sections.get("review", pd.DataFrame())
                _info(f"Built review section from analysis: {len(review_df)} products")
        except Exception as e:
            _warn(f"Could not build review section: {e}")

    if review_df.empty:
        _warn("No review products available for AI mini run")
        return {"status": "skipped", "reason": "No review products"}

    # Take safe sample of 30
    sample_size = min(30, len(review_df))
    sample_df = review_df.head(sample_size).copy()
    _info(f"Using {sample_size} products for AI mini run")

    # Check AI availability
    from conf.settings import Settings
    settings = Settings.load()
    if not settings.any_ai_configured:
        _warn("AI not configured - simulating dry run")
        return {"status": "dry_run", "sample_size": sample_size}

    # Run AI Router on sample
    try:
        from services.ai_service import AIService, default_providers
        from services.ai_router_service import AIRouterService

        ai_svc = AIService(settings=settings)
        router = AIRouterService(ai_svc, confidence_threshold=0.85)

        t0 = time.time()
        result = router.route_review(sample_df, batch_size=8)
        duration = time.time() - t0

        stats = {
            "status": "success",
            "sample_size": sample_size,
            "upgraded_to_match": len(result.upgraded),
            "confirmed_missing": len(result.to_missing),
            "stayed_review": len(result.stayed_review),
            "errors": len(result.errors),
            "duration_s": round(duration, 1),
            "accounting_check": (len(result.upgraded) + len(result.to_missing) + len(result.stayed_review)) == sample_size,
        }

        total_routed = stats["upgraded_to_match"] + stats["confirmed_missing"] + stats["stayed_review"]
        if total_routed == sample_size:
            _pass(f"AI routing completed: {sample_size} in, {total_routed} out (0% loss)")
        else:
            _fail(f"ACCOUNTING ERROR: {sample_size} in, {total_routed} out!")

        _info(f"Upgraded to MATCH: {stats['upgraded_to_match']}")
        _info(f"Confirmed MISSING: {stats['confirmed_missing']}")
        _info(f"Stayed in REVIEW:  {stats['stayed_review']}")
        _info(f"Errors:            {stats['errors']}")
        _info(f"Duration:          {stats['duration_s']}s")

        if result.errors:
            for err in result.errors[:3]:
                _warn(f"  Error: {err[:100]}")

        return stats

    except Exception as e:
        _fail(f"AI Mini Run failed: {e}")
        return {"status": "error", "error": str(e)}


# ============================================================================
#  CHECK 4: Closed-Loop Accounting
# ============================================================================
def check_closed_loop() -> bool:
    _header("CHECK 4: Closed-Loop Accounting")

    import json
    import pandas as pd
    from conf.constants import DATA_DIR, COMPETITOR_DB_PATH

    # اللقطة صارت مجلّداً (صيغة 3) — جسر التوافق يعيد نفس البنية القديمة.
    from ui.state_manager import load_snapshot_raw, snapshot_exists

    if not snapshot_exists():
        _warn("No snapshot file - cannot verify closed loop")
        return False

    try:
        from ui.state_manager import _deserialize_snapshot
        snap = _deserialize_snapshot(load_snapshot_raw())
        sections = snap.get("sections", {})
        missing_df = snap.get("missing_df")

        if not sections:
            _warn("Empty sections in snapshot - cannot verify")
            return False

        from engines.closed_loop_engine import (
            load_competitor_universe,
            reconcile_competitor_universe,
        )

        db_path = str(COMPETITOR_DB_PATH)
        universe = load_competitor_universe(db_path)
        ledger = reconcile_competitor_universe(
            universe, sections, missing_df, context="e2e_health_check",
        )

        _info(f"Total input:       {ledger.total_input:,}")
        _info(f"Confirmed match:   {ledger.confirmed_match:,}")
        _info(f"Confirmed missing: {ledger.confirmed_missing:,}")
        _info(f"AI review pending: {ledger.ai_review_pending:,}")
        _info(f"Excluded (noise):  {ledger.excluded_noise:,}")
        _info(f"Balanced:          {ledger.balanced}")

        if ledger.balanced:
            _pass("Closed-loop accounting: BALANCED (0% leakage)")
        else:
            gap = ledger.total_input - (ledger.confirmed_match + ledger.confirmed_missing + ledger.ai_review_pending + ledger.excluded_noise)
            _fail(f"Closed-loop IMBALANCED! Gap = {gap}")

        return ledger.balanced

    except Exception as e:
        _fail(f"Closed-loop check failed: {e}")
        return False


# ============================================================================
#  CHECK 5: UI Link Integrity (target="_blank")
# ============================================================================
def check_ui_links() -> bool:
    _header("CHECK 5: UI Link Integrity")

    comp_card = os.path.join(ROOT, "ui", "components", "comparison_card.py")
    with open(comp_card, encoding="utf-8") as f:
        code = f.read()

    # Check that _link function uses HTML <a> with target="_blank"
    has_target_blank = 'target="_blank"' in code
    has_noopener = 'rel="noopener"' in code
    has_a_href = '<a href=' in code

    # Check there are no bare markdown links [text](url) in the rendered HTML
    # (exclude docstrings/comments)
    tree = ast.parse(code)
    md_link_in_html = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            # Check if string contains markdown link AND looks like HTML
            if re.search(r'\[.+\]\(http', node.value) and '<' not in node.value:
                md_link_in_html = True

    if has_target_blank and has_a_href and has_noopener:
        _pass("comparison_card.py uses HTML <a target='_blank' rel='noopener'>")
    else:
        _fail("Missing target='_blank' or <a> tags in comparison_card.py")

    if not md_link_in_html:
        _pass("No markdown [name](url) found in rendered HTML strings")
    else:
        _warn("Found markdown link syntax in HTML strings")

    # Check _link function has Google search fallback
    has_fallback = "_SEARCH" in code and "quote_plus" in code
    if has_fallback:
        _pass("Links have Google search fallback (never broken)")
    else:
        _warn("No fallback search URL for missing links")

    return has_target_blank and has_a_href and not md_link_in_html


# ============================================================================
#  CHECK 6: Make Send Button Integrity
# ============================================================================
def check_make_button() -> bool:
    _header("CHECK 6: Make Send Button Code Integrity")

    price_action = os.path.join(ROOT, "ui", "components", "price_action.py")
    with open(price_action, encoding="utf-8") as f:
        code = f.read()

    checks = {
        "try/except around webhook call": "try:" in code and "except Exception" in code,
        "NO field validation": 'products[0].get("NO")' in code,
        "state.hide() for visual removal": "state.hide(" in code,
        "state.mark_price_processed()": "state.mark_price_processed(" in code,
        "state.log_action()": "state.log_action(" in code,
        "state.persist_results()": "state.persist_results()" in code,
        "st.rerun() for instant evacuation": "st.rerun()" in code,
        "Error shows response.text": "error_body" in code or "result.get('body')" in code,
    }

    all_pass = True
    for check, passed in checks.items():
        if passed:
            _pass(check)
        else:
            _fail(check)
            all_pass = False

    # Check card_actions.py too
    card_actions = os.path.join(ROOT, "ui", "components", "card_actions.py")
    with open(card_actions, encoding="utf-8") as f:
        ca_code = f.read()

    if "st.rerun()" in ca_code:
        _pass("card_actions.py: st.rerun() for missing products evacuation")
    else:
        _fail("card_actions.py: missing st.rerun()")
        all_pass = False

    return all_pass


# ============================================================================
#  MAIN
# ============================================================================
def main():
    t0 = time.time()
    print("\n" + "=" * 70)
    print("  MAHWOUS PRICING v2 — END-TO-END HEALTH CHECK")
    print(f"  {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    results = {}
    results["ai_config"] = check_ai_config()
    results["ai_router"] = check_ai_router_service()
    results["ai_mini_run"] = check_ai_mini_run()
    results["closed_loop"] = check_closed_loop()
    results["ui_links"] = check_ui_links()
    results["make_button"] = check_make_button()

    # ── Final Report ──
    _header("FINAL REPORT")
    total = len(results)
    passed = sum(1 for v in results.values() if v is True or (isinstance(v, dict) and v.get("status") == "success"))
    warns = sum(1 for v in results.values() if isinstance(v, dict) and v.get("status") in ("skipped", "dry_run"))

    for name, result in results.items():
        if result is True:
            status = "PASS"
        elif isinstance(result, dict):
            status = result.get("status", "?").upper()
        else:
            status = "FAIL"
        print(f"  {name:20s} : {status}")

    print(f"\n  TOTAL: {passed} PASS / {warns} WARN / {total - passed - warns} FAIL")
    print(f"  Duration: {time.time() - t0:.1f}s")
    print("=" * 70)


if __name__ == "__main__":
    main()
