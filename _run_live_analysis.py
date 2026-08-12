"""_run_live_analysis.py — تشغيل التحليل الفعلي + تقرير قمع المنافسين (headless)."""
import glob
import os
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _is_salla_product_export(path):
    """يتأكّد أن الملف تصدير منتجات سلة فعلي (لا SEO/ماركات) عبر صفّ الميتا أو أعمدته."""
    try:
        import pandas as pd
        peek = pd.read_excel(path, header=None, nrows=1)
        if str(peek.iloc[0, 0]).strip() == "بيانات المنتج":
            return True
        cols = {str(c) for c in pd.read_excel(path, nrows=0).columns}
        return ("سعر المنتج" in cols) and any(("اسم المنتج" in c or "أسم المنتج" in c) for c in cols)
    except Exception:
        return False


def find_latest_catalog(explicit=None):
    """يختار أحدث تصدير منتجات سلة فعلي؛ المسار الصريح يفوز دائماً (أدقّ خيار)."""
    if explicit:
        if os.path.exists(explicit):
            return explicit
        raise FileNotFoundError(f"catalog not found: {explicit}")
    dl = os.path.join(os.path.expanduser("~"), "Downloads")
    patterns = [
        os.path.join(dl, "*", "mahwous_*_products.xlsx"),
        os.path.join(dl, "mahwous_*_products.xlsx"),
        os.path.join(dl, "*Mahwous Perfumes تصدير المنتجات*.xlsx"),
    ]
    files = []
    for p in patterns:
        files.extend(glob.glob(p))
    files = sorted(set(files), key=os.path.getmtime, reverse=True)
    # أحدث ملف يجتاز فحص «تصدير منتجات سلة» — يمنع الوقوع على SEO/ماركات/كتالوج قديم
    for f in files:
        if _is_salla_product_export(f):
            return f
    if files:
        return files[0]
    raise FileNotFoundError("no catalog found")


def main():
    t0 = time.time()
    print("=" * 70)
    print("  MAHWOUS v2 -- Live Analysis + Competitor Funnel")
    print("=" * 70)

    explicit = sys.argv[1] if len(sys.argv) > 1 else None
    catalog_path = find_latest_catalog(explicit)
    mtime = time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(catalog_path)))
    print(f"\n[1/7] CATALOG: {os.path.basename(catalog_path)}")
    print(f"       Modified: {mtime}  |  Size: {os.path.getsize(catalog_path):,} bytes")

    print("\n[2/7] CONTAINER...")
    from bootstrap import build_container, run_missing_analysis, run_pricing_analysis
    from services.catalog_service import load_catalog

    container = build_container()
    print(f"       AI: {container.ai.any_configured}")

    print(f"\n[3/7] LOADING catalog...")
    our_df = load_catalog(catalog_path)
    N = len(our_df)
    print(f"       Products: {N:,}")
    assert N > 0

    print(f"\n[4/7] MISSING ANALYSIS...")
    missing_df, mstats = run_missing_analysis(container, our_df)
    print(f"       Missing: {len(missing_df):,}")

    print(f"\n[5/7] PRICING ANALYSIS + AI ROUTER...")
    t2 = time.time()
    sections, result, missing_clean, astats = run_pricing_analysis(
        container, our_df, missing_df=missing_df,
    )
    t_price = time.time() - t2
    print(f"       Duration: {t_price:.1f}s")

    # === DISTRIBUTION REPORT ===
    import pandas as pd
    print("\n" + "=" * 70)
    print("  DISTRIBUTION REPORT")
    print("=" * 70)
    total_distributed = 0
    for key in ("price_raise", "price_lower", "approved", "review", "excluded"):
        df = sections.get(key, pd.DataFrame())
        count = len(df)
        total_distributed += count
        emoji = {"price_raise": "RED", "price_lower": "GRN", "approved": "OK ",
                 "review": "REV", "excluded": "EXC"}
        print(f"  [{emoji.get(key, '???')}] {key:15s}: {count:>6,}")
    print(f"  {'':15s}  {'':>6s} --------")
    print(f"  {'TOTAL':15s}  {total_distributed:>6,}")
    miss_count = len(missing_clean) if missing_clean is not None else 0
    print(f"  {'MISSING':15s}  {miss_count:>6,}")

    # === AI ROUTER STATS ===
    ai_keys = {k: v for k, v in astats.items() if k.startswith("ai_")}
    if ai_keys:
        print(f"\n  AI ROUTER:")
        for k, v in ai_keys.items():
            print(f"    {k}: {v}")

    # === GAP CHECK ===
    gap = N - total_distributed
    print(f"\n  GAP CHECK:")
    print(f"    Input:       {N:,}")
    print(f"    Distributed: {total_distributed:,}")
    print(f"    Gap:         {gap}")
    if gap == 0:
        print(f"    >> PERFECT: 0% data loss!")
    else:
        print(f"    >> WARNING: {gap} products unaccounted")

    # === RECONCILIATION ===
    if result.reconciliation:
        r = result.reconciliation
        print(f"\n  RECONCILIATION:")
        print(f"    All count:   {r.all_count:,}")
        print(f"    Sum buckets: {r.sum_buckets:,}")
        print(f"    Gap:         {r.all_count - r.sum_buckets}")
        print(f"    Duplicates:  {r.duplicate_count}")

    # === [6/7] COMPETITOR FUNNEL ===
    print(f"\n[6/7] COMPETITOR FUNNEL REPORT...")
    from conf.constants import COL_COMP_NAME, COL_COMP_PRICE
    from bootstrap import load_competitor_dfs
    from conf.constants import COMPETITOR_DB_PATH

    # Load total competitors from DB
    db_path = str(COMPETITOR_DB_PATH)
    if os.path.exists(db_path):
        comp_dfs = load_competitor_dfs(db_path)
        total_comp_rows = sum(len(df) for df in comp_dfs.values())
        total_comp_sites = len(comp_dfs)
        print(f"       Total competitor products in DB: {total_comp_rows:,}")
        print(f"       Competitor sites: {total_comp_sites}")
    else:
        total_comp_rows = 0
        print(f"       DB not found: {db_path}")

    # Count competitors matched in sections
    matched_comps = set()
    for key in ("price_raise", "price_lower", "approved", "review", "excluded"):
        df = sections.get(key, pd.DataFrame())
        if COL_COMP_NAME in df.columns:
            names = df[COL_COMP_NAME].dropna().astype(str).str.strip().str.lower()
            names = names[names != ""]
            matched_comps.update(names.tolist())

    # Count missing competitors
    missing_comps = set()
    if missing_clean is not None and not missing_clean.empty:
        if COL_COMP_NAME in missing_clean.columns:
            names = missing_clean[COL_COMP_NAME].dropna().astype(str).str.strip().str.lower()
            names = names[names != ""]
            missing_comps.update(names.tolist())

    # Summary
    overlap = matched_comps & missing_comps
    all_accounted = matched_comps | missing_comps
    unaccounted = total_comp_rows - len(all_accounted)

    print(f"\n  COMPETITOR FUNNEL:")
    print(f"    Total in DB:           {total_comp_rows:>8,}")
    print(f"    Matched to our prods:  {len(matched_comps):>8,} (in sections)")
    print(f"    In missing list:       {len(missing_comps):>8,} (missing_df)")
    print(f"    Overlap (dup):         {len(overlap):>8,}")
    print(f"    Total accounted:       {len(all_accounted):>8,}")
    print(f"    Unaccounted:           {unaccounted:>8,}")
    if total_comp_rows > 0:
        pct = len(all_accounted) / total_comp_rows * 100
        print(f"    Coverage:              {pct:>7.1f}%")

    # === [7/7] PERSIST ===
    print(f"\n[7/7] PERSISTING...")
    from ui.state_manager import AppState

    state = AppState()
    state.our_catalog = our_df
    state.sections = sections
    state.missing_df = missing_clean
    state.analysis_results = result
    saved = state.persist_results()
    if saved:
        from ui.state_manager import snapshot_size_bytes  # اللقطة صارت مجلّداً (صيغة 3)
        size = snapshot_size_bytes()
        print(f"       SAVED: {size:,} bytes")
    else:
        print(f"       ERROR: persist failed")

    total_time = time.time() - t0
    print(f"\n{'=' * 70}")
    print(f"  COMPLETE in {total_time:.1f}s")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    main()
