"""Runs the full synthetic pipeline 5 times (simulating 5 days of always-on
monitoring) and writes one combined JSON per run to site/data/run_N.json.

Usage: python run_all.py
"""

from __future__ import annotations

import json
import pathlib
import sys

import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from generate_data import generate_all
from lineage import build_lineage_events
from quality_checks import run_quality_checks
from transform import build_regulatory_extract

NUM_RUNS = 5
OUTPUT_DIR = pathlib.Path(__file__).parent.parent / "site" / "data"


def _json_default(obj):
    if isinstance(obj, (pd.Timestamp,)):
        return obj.isoformat()
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    if pd.isna(obj):
        return None
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


def run_pipeline(seed: int, run_index: int) -> dict:
    data = generate_all(seed=seed, run_index=run_index)
    recon = build_regulatory_extract(
        data["loan_originations"], data["account_master"], data["transactions"], data["customer_ids"]
    )
    quality = run_quality_checks(
        data["loan_originations"],
        data["account_master"],
        data["transactions"],
        data["customer_ids"],
        data["as_of_date"],
    )
    lineage_events = build_lineage_events(
        data["loan_originations"],
        data["account_master"],
        data["transactions"],
        recon["extract"],
        quality["tables"],
        run_index=run_index,
        as_of_date=data["as_of_date"],
    )

    return {
        "run_id": run_index,
        "as_of_date": data["as_of_date"].isoformat(),
        "lineage_events": lineage_events,
        "quality": quality,
        "source_row_counts": {
            "loan_originations": int(len(data["loan_originations"])),
            "account_master": int(len(data["account_master"])),
            "transactions": int(len(data["transactions"])),
            "regulatory_extract": int(len(recon["extract"])),
        },
    }


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    summaries = []
    for run_index in range(1, NUM_RUNS + 1):
        seed = 1000 + run_index
        result = run_pipeline(seed=seed, run_index=run_index)
        out_path = OUTPUT_DIR / f"run_{run_index}.json"
        out_path.write_text(json.dumps(result, indent=2, default=_json_default))
        summaries.append(
            (run_index, result["quality"]["aggregate_score"], result["quality"]["aggregate_verdict"])
        )
        print(f"wrote {out_path} -- aggregate score {result['quality']['aggregate_score']} "
              f"({result['quality']['aggregate_verdict']})")

    print("\nSummary across runs:")
    for run_index, score, verdict in summaries:
        print(f"  run_{run_index}: {score} ({verdict})")


if __name__ == "__main__":
    main()
