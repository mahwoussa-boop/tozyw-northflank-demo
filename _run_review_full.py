"""Headless full-review AI classifier (Stage B full run). Resumable via disk cache.

Reads review rows from the live snapshot, classifies each (competitor vs candidate)
with few-shot + semantic guard, writes:
  - data/reconcile/review_ai_DUPLICATE.csv   (consumed by ui.reconcile)
  - data/reconcile/review_ai_decisions.jsonl (learning log)
  - data/reconcile/review_ai_progress.log    (monitorable)
"""
import json, sys, time, datetime
from pathlib import Path
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import pandas as pd
from conf.constants import DATA_DIR
from services.ai_service import AIService
from ui.review_ai import (classify_rows, build_decision_records, append_jsonl,
                          learned_examples, DUPLICATE, UNIQUE, UNSURE, _DECISIONS_FILE)

RECON = Path(DATA_DIR) / "reconcile"
PROG = RECON / "review_ai_progress.log"
CACHE = RECON / "review_ai_cache.json"


def log(msg):
    line = f"{datetime.datetime.now():%H:%M:%S} {msg}"
    with open(PROG, "a", encoding="utf-8") as h:
        h.write(line + "\n")


class DiskCache:
    """كاش قرصي (get/set) ⇒ التشغيل قابل للاستئناف وإعادة التشغيل مجانية."""
    def __init__(self, path):
        self.path = Path(path); self.data = {}; self.dirty = 0
        if self.path.exists():
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    self.data = json.load(f)
            except Exception:
                self.data = {}
    def get(self, key): return self.data.get(key)
    def set(self, key, value):
        self.data[key] = value; self.dirty += 1
        if self.dirty >= 40: self.flush()
    def flush(self):
        if self.dirty:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False)
            self.dirty = 0


def main():
    PROG.unlink(missing_ok=True)
    # اللقطة صارت مجلّداً (صيغة 3) — جسر التوافق + التفكيك يعطيان DataFrame فعلياً
    # (السطر السابق كان يمرّر القاموس الخام ``{"__type__": ...}`` فيسقط على المفتاح).
    from ui.state_manager import _deserialize_snapshot, load_snapshot_raw

    snap = _deserialize_snapshot(load_snapshot_raw())
    mdf = snap["missing_df"]
    rows = mdf[mdf["مستوى_الثقة"].astype(str) == "review"].to_dict("records")
    total = len(rows)
    log(f"START review full-run: {total} rows, cache={'warm' if CACHE.exists() else 'cold'}")
    cache = DiskCache(CACHE)
    ai = AIService(cache=cache)
    examples = learned_examples(DATA_DIR)
    log(f"few-shot examples: {len(examples)}")
    t0 = time.time()

    def progress(done, _total):
        if done % 160 == 0 or done == total:
            cache.flush()
            rate = done / max(time.time() - t0, 1)
            eta = (total - done) / max(rate, 0.01) / 60
            log(f"  {done}/{total} ({100*done/total:.1f}%) ~{eta:.0f}min left")

    try:
        verdicts = classify_rows(rows, ai, examples=examples, guard=True,
                                 progress_cb=progress)
    except Exception as exc:
        cache.flush(); log(f"ERROR: {exc}"); raise
    cache.flush()

    dup = verdicts.count(DUPLICATE); uniq = verdicts.count(UNIQUE); uns = verdicts.count(UNSURE)
    log(f"DONE in {(time.time()-t0)/60:.1f}min | مكرر={dup} فريد={uniq} غير_مؤكد={uns}")

    # 1) decision log (learning)
    append_jsonl(RECON / _DECISIONS_FILE,
                 build_decision_records(rows, verdicts, source="ai"))
    # 2) confirmed-duplicates CSV (consumed by ui.reconcile.load_reconcile_frames)
    dup_rows = [r for r, v in zip(rows, verdicts) if v == DUPLICATE]
    out = pd.DataFrame(dup_rows) if dup_rows else pd.DataFrame()
    out.to_csv(RECON / "review_ai_DUPLICATE.csv", index=False, encoding="utf-8-sig")
    # mirror into repo data/reconcile too
    repo = ROOT / "data" / "reconcile"
    repo.mkdir(parents=True, exist_ok=True)
    out.to_csv(repo / "review_ai_DUPLICATE.csv", index=False, encoding="utf-8-sig")
    log(f"WROTE review_ai_DUPLICATE.csv: {len(out)} rows")
    print("OK", dup, uniq, uns)


if __name__ == "__main__":
    main()
