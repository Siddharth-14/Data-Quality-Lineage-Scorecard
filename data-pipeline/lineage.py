"""Emits OpenLineage-formatted events for the regulatory extract transform.

We don't stand up a live Marquez server -- instead we hand-build real
OpenLineage RunEvent JSON (the same schema OpenLineage-instrumented jobs
emit in production) so the frontend can render a lineage graph from actual
OpenLineage event data rather than a hand-drawn diagram.

Reference: https://openlineage.io/docs/spec/object-model
"""

from __future__ import annotations

import datetime as dt
import uuid

import pandas as pd

NAMESPACE = "synthetic-bank"
JOB_NAME = "build_regulatory_extract"
PRODUCER = "https://github.com/openlineage/openlineage/tree/main/client/python"
SCHEMA_URL = "https://openlineage.io/spec/2-0-2/OpenLineage.json"

SOURCE_SYSTEMS = {
    "loan_originations": "loan-origination-system",
    "account_master": "core-banking-system",
    "transactions": "transaction-processing-system",
}


def _schema_facet(df: pd.DataFrame) -> dict:
    fields = []
    for col in df.columns:
        dtype = str(df[col].dtype)
        if dtype.startswith("float"):
            dtype = "double"
        elif dtype.startswith("int"):
            dtype = "long"
        elif dtype.startswith("datetime"):
            dtype = "timestamp"
        else:
            dtype = "string"
        fields.append({"name": col, "type": dtype})
    return {"_producer": PRODUCER, "_schemaURL": f"{SCHEMA_URL}#/definitions/SchemaDatasetFacet", "fields": fields}


def _data_quality_facet(table_quality: dict | None) -> dict | None:
    if table_quality is None:
        return None
    return {
        "_producer": PRODUCER,
        "_schemaURL": f"{SCHEMA_URL}#/definitions/DataQualityMetricsInputDatasetFacet",
        "rowCount": table_quality["row_count"],
        "score": table_quality["score"],
        "verdict": "PASS" if table_quality["score"] >= 90 else ("AT RISK" if table_quality["score"] >= 75 else "FAIL"),
        "failedChecks": [c["category"] for c in table_quality["checks"] if c["score"] < 99.5],
    }


def _dataset(name: str, namespace: str, df: pd.DataFrame, quality: dict | None) -> dict:
    facets = {"schema": _schema_facet(df)}
    dq_facet = _data_quality_facet(quality)
    if dq_facet is not None:
        facets["dataQualityMetrics"] = dq_facet
    return {"namespace": namespace, "name": name, "facets": facets}


def build_lineage_events(
    loans: pd.DataFrame,
    accounts: pd.DataFrame,
    transactions: pd.DataFrame,
    extract: pd.DataFrame,
    quality_tables: dict,
    run_index: int,
    as_of_date: dt.date,
) -> list[dict]:
    """Return a [START, COMPLETE] pair of OpenLineage RunEvents."""
    run_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"synthetic-bank-run-{run_index}"))
    event_time_start = dt.datetime.combine(as_of_date, dt.time(2, 0), tzinfo=dt.timezone.utc)
    event_time_end = event_time_start + dt.timedelta(minutes=7)

    inputs = [
        _dataset("loan_originations", SOURCE_SYSTEMS["loan_originations"], loans, quality_tables.get("loan_originations")),
        _dataset("account_master", SOURCE_SYSTEMS["account_master"], accounts, quality_tables.get("account_master")),
        _dataset("transactions", SOURCE_SYSTEMS["transactions"], transactions, quality_tables.get("transactions")),
    ]
    output = _dataset("regulatory_extract", NAMESPACE, extract, quality_tables.get("regulatory_extract"))

    job = {"namespace": NAMESPACE, "name": JOB_NAME, "facets": {}}
    run = {"runId": run_id, "facets": {}}

    base_event = {
        "eventType": "START",
        "eventTime": event_time_start.isoformat(),
        "producer": PRODUCER,
        "schemaURL": f"{SCHEMA_URL}#/definitions/RunEvent",
        "run": run,
        "job": job,
        "inputs": inputs,
        "outputs": [],
    }
    complete_event = {
        "eventType": "COMPLETE",
        "eventTime": event_time_end.isoformat(),
        "producer": PRODUCER,
        "schemaURL": f"{SCHEMA_URL}#/definitions/RunEvent",
        "run": run,
        "job": job,
        "inputs": inputs,
        "outputs": [output],
    }
    return [base_event, complete_event]


if __name__ == "__main__":
    from generate_data import generate_all
    from quality_checks import run_quality_checks
    from transform import build_regulatory_extract

    data = generate_all(seed=1, run_index=1)
    quality = run_quality_checks(
        data["loan_originations"], data["account_master"], data["transactions"], data["customer_ids"], data["as_of_date"]
    )
    recon = build_regulatory_extract(
        data["loan_originations"], data["account_master"], data["transactions"], data["customer_ids"]
    )
    events = build_lineage_events(
        data["loan_originations"],
        data["account_master"],
        data["transactions"],
        recon["extract"],
        quality["tables"],
        run_index=1,
        as_of_date=data["as_of_date"],
    )
    import json

    print(json.dumps(events[1], indent=2, default=str)[:2000])
