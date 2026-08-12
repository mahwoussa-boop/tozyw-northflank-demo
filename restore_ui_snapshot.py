"""Restore the persisted UI snapshot from the shipped analysis caches.

The deployment seed contains durable databases and the two serialized DataFrames used
by the analysis pipeline.  The Streamlit UI, however, restores prior results from
``DATA_DIR/ui_session/_meta.json``.  This tool reconstructs that UI snapshot only
when it is missing; it never overwrites a valid owner snapshot.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


_DECISION_PREFIXES = {
    "price_raise": "🔴 سعر أعلى",
    "price_lower": "🟢 سعر أقل",
    "approved": "✅ موافق",
    "review": "⚠️ تحت المراجعة",
    "excluded": "⚪ مستبعد",
}

_CATALOG_COLUMNS = (
    "المنتج",
    "معرف_المنتج",
    "السعر",
    "الماركة",
    "الحجم",
    "النوع",
    "الجنس",
    "صورة_منتجنا",
    "رابط_منتجنا",
)


def load_cached_frame(path: Path) -> Any:
    """Read one serialized DataFrame written by ``save_cache``.

    The cache is an outer JSON object containing a DataFrame's ``orient=split``
    payload as a string.  Validation is intentionally strict: malformed cache
    inputs must not create a misleading empty dashboard snapshot.
    """
    import pandas as pd

    with path.open("r", encoding="utf-8") as handle:
        outer = json.load(handle)
    payload = outer.get("df") if isinstance(outer, dict) else None
    if not isinstance(payload, dict) or payload.get("__type__") != "DataFrame":
        raise ValueError(f"unsupported cache structure: {path.name}")
    data = payload.get("data")
    if not isinstance(data, str) or not data:
        raise ValueError(f"empty DataFrame payload: {path.name}")
    frame = pd.read_json(io.StringIO(data), orient="split")
    if frame.empty:
        raise ValueError(f"empty DataFrame: {path.name}")
    return frame


def _build_sections(pricing: Any) -> dict[str, Any]:
    """Partition the saved pricing DataFrame exactly once by its displayed decision."""
    if "القرار" not in pricing.columns:
        raise ValueError("pricing cache has no القرار column")
    decisions = pricing["القرار"].fillna("").astype(str)
    sections: dict[str, Any] = {}
    assigned = 0
    for key, prefix in _DECISION_PREFIXES.items():
        section = pricing[decisions.str.startswith(prefix)].copy().reset_index(drop=True)
        sections[key] = section
        assigned += len(section)
    if assigned != len(pricing):
        unknown = sorted(set(decisions) - {
            value for value in decisions
            if any(value.startswith(prefix) for prefix in _DECISION_PREFIXES.values())
        })
        raise ValueError(
            f"pricing decisions do not cover all rows: assigned={assigned}, "
            f"total={len(pricing)}, unknown={unknown[:5]}"
        )
    return sections


def _build_catalog(pricing: Any) -> Any:
    """Recover the analyzed store catalog from the one-row-per-product pricing output."""
    available = [column for column in _CATALOG_COLUMNS if column in pricing.columns]
    if not {"المنتج", "معرف_المنتج", "السعر"}.issubset(available):
        raise ValueError("pricing cache cannot reconstruct the store catalog")
    catalog = pricing.loc[:, available].copy()
    catalog = catalog.drop_duplicates(subset=["معرف_المنتج"], keep="first")
    catalog = catalog.reset_index(drop=True)
    return catalog


def _progress_metadata(data_dir: Path) -> tuple[datetime, float, str | None]:
    progress_path = data_dir / "analysis_progress.json"
    if not progress_path.exists():
        return datetime.now(), 0.0, None
    try:
        with progress_path.open("r", encoding="utf-8") as handle:
            progress = json.load(handle)
        finished_at = float(progress.get("finished_at") or progress.get("updated_at") or 0)
        created = datetime.fromtimestamp(finished_at) if finished_at > 0 else datetime.now()
        duration = float(progress.get("duration_sec") or 0.0)
        run_id = str(progress.get("run_id") or "").strip() or None
        return created, duration, run_id
    except Exception:
        return datetime.now(), 0.0, None


def restore_snapshot(data_dir: Path) -> dict[str, int | str]:
    """Create ``ui_session`` from existing caches without replacing an existing snapshot."""
    data_dir = data_dir.resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    os.environ["DATA_DIR"] = str(data_dir)

    # Import after DATA_DIR is set: constants and StateManager bind this once.
    from core.enums import SectionType
    from core.models import AnalysisResult, ReconciliationReport
    from services.audit_service import dedup_missing_vs_matched
    from ui.state_manager import AppState, snapshot_exists

    if snapshot_exists():
        return {"status": "exists"}

    pricing = load_cached_frame(data_dir / "pricing_cache.json")
    missing = load_cached_frame(data_dir / "missing_cache.json")
    sections = _build_sections(pricing)
    missing_clean, removed = dedup_missing_vs_matched(sections, missing)
    if missing_clean is None:
        raise ValueError("missing cache could not be restored")

    bucket_counts = {key: len(frame) for key, frame in sections.items()}
    reconciliation = ReconciliationReport.from_sections(
        all_count=len(pricing), bucket_counts=bucket_counts,
    )
    reconciliation.assert_balanced()

    missing_confirmed = 0
    if "مستوى_الثقة" in missing_clean.columns:
        missing_confirmed = int(
            (missing_clean["مستوى_الثقة"].fillna("").astype(str) == "green").sum()
        )
    created_at, duration_sec, run_id = _progress_metadata(data_dir)
    result = AnalysisResult(
        section_counts={
            SectionType.PRICE_RAISE: bucket_counts["price_raise"],
            SectionType.PRICE_LOWER: bucket_counts["price_lower"],
            SectionType.APPROVED: bucket_counts["approved"],
            SectionType.REVIEW: bucket_counts["review"],
            SectionType.EXCLUDED: bucket_counts["excluded"],
            SectionType.MISSING: missing_confirmed,
        },
        total=len(pricing),
        missing_count=missing_confirmed,
        reconciliation=reconciliation,
        job_id=run_id,
        created_at=created_at,
        duration_sec=duration_sec,
    )

    state = AppState(
        our_catalog=_build_catalog(pricing),
        sections=sections,
        missing_df=missing_clean,
        analysis_results=result,
    )
    if not state.persist_results(full=True):
        raise RuntimeError("failed to persist reconstructed UI snapshot")

    return {
        "status": "created",
        "pricing_rows": len(pricing),
        "missing_rows": len(missing_clean),
        "missing_confirmed": missing_confirmed,
        "deduped_missing_rows": int(removed),
        **bucket_counts,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Rebuild UI snapshot from analysis caches")
    parser.add_argument(
        "--data-dir",
        default=os.environ.get("DATA_DIR", "/data"),
        help="Directory containing analysis cache files",
    )
    args = parser.parse_args()
    result = restore_snapshot(Path(args.data_dir))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"UI snapshot restore skipped: {exc}", file=sys.stderr)
        raise
