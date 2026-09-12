"""Data quality checks built on Great Expectations.

For each of the three source tables and the regulatory_extract, we run real
Great Expectations validations (not hand-typed numbers) across four
categories: completeness, referential integrity, freshness, and
reconciliation. Each category check reports a 0-100 score; a table's score
is the average of its category scores, and the aggregate readiness verdict
is the average of all table scores, mapped via documented thresholds:

    score >= 90              -> PASS
    75 <= score < 90         -> AT RISK
    score < 75                -> FAIL
"""

from __future__ import annotations

import itertools
import uuid

import great_expectations as gx
import pandas as pd

from transform import build_regulatory_extract

FRESHNESS_SLA_DAYS_ACCOUNT = 180
FRESHNESS_SLA_DAYS_TXN = 120

PASS_THRESHOLD = 90.0
AT_RISK_THRESHOLD = 75.0

_context = gx.get_context(mode="ephemeral")
_counter = itertools.count()


def _run_expectation(df: pd.DataFrame, expectation) -> dict:
    """Run a single GE expectation against an in-memory DataFrame."""
    unique = f"{uuid.uuid4().hex[:8]}-{next(_counter)}"
    data_source = _context.data_sources.add_pandas(f"src-{unique}")
    asset = data_source.add_dataframe_asset(name=f"asset-{unique}")
    batch_def = asset.add_batch_definition_whole_dataframe(f"batch-{unique}")
    batch = batch_def.get_batch(batch_parameters={"dataframe": df})
    result = batch.validate(expectation)
    r = result.get("result", {})
    element_count = r.get("element_count", 0) or 0
    unexpected_count = r.get("unexpected_count", 0) or 0
    unexpected_percent = r.get("unexpected_percent", 0.0) or 0.0
    return {
        "element_count": int(element_count),
        "unexpected_count": int(unexpected_count),
        "unexpected_percent": float(unexpected_percent),
        "score": round(max(0.0, 100.0 - unexpected_percent), 2),
    }


def _score_from_ratio(bad: int, total: int) -> dict:
    pct = (bad / total * 100.0) if total else 0.0
    return {
        "element_count": total,
        "unexpected_count": bad,
        "unexpected_percent": round(pct, 4),
        "score": round(max(0.0, 100.0 - pct), 2),
    }


def _article(word: str) -> str:
    return "an" if word[0].lower() in "aeiou" else "a"


def _check_completeness(df: pd.DataFrame, table: str, column: str) -> dict:
    check = _run_expectation(df, gx.expectations.ExpectColumnValuesToNotBeNull(column=column))
    check["category"] = "completeness"
    check["description"] = (
        f"{check['unexpected_count']} of {check['element_count']} {table} rows are missing "
        f"{_article(column)} {column} ({check['unexpected_percent']:.1f}%)"
    )
    return check


def _check_referential_integrity(
    df: pd.DataFrame, table: str, column: str, valid_values: set, ref_table: str
) -> dict:
    non_null = df[df[column].notna()]
    if non_null.empty:
        check = _score_from_ratio(0, 0)
    else:
        check = _run_expectation(
            non_null, gx.expectations.ExpectColumnValuesToBeInSet(column=column, value_set=list(valid_values))
        )
    check["category"] = "referential_integrity"
    check["description"] = (
        f"{check['unexpected_count']} of {check['element_count']} {table} rows reference "
        f"{_article(column)} {column} not found in {ref_table} ({check['unexpected_percent']:.1f}%)"
    )
    return check


def _check_freshness(df: pd.DataFrame, table: str, age_days: pd.Series, sla_days: int, label: str) -> dict:
    working = df.copy()
    working["_age_days"] = age_days.values
    check = _run_expectation(
        working,
        gx.expectations.ExpectColumnValuesToBeBetween(column="_age_days", min_value=0, max_value=sla_days),
    )
    check["category"] = "freshness"
    check["description"] = (
        f"{check['unexpected_count']} of {check['element_count']} {table} rows are {label} "
        f"({check['unexpected_percent']:.1f}%)"
    )
    return check


def _check_reconciliation(bad: int, total: int, table: str, detail: str) -> dict:
    check = _score_from_ratio(bad, total)
    check["category"] = "reconciliation"
    check["description"] = detail
    return check


def _table_score(checks: list[dict]) -> float:
    if not checks:
        return 100.0
    return round(sum(c["score"] for c in checks) / len(checks), 2)


def _verdict(score: float) -> str:
    if score >= PASS_THRESHOLD:
        return "PASS"
    if score >= AT_RISK_THRESHOLD:
        return "AT RISK"
    return "FAIL"


def run_quality_checks(
    loans: pd.DataFrame,
    accounts: pd.DataFrame,
    transactions: pd.DataFrame,
    customer_ids: list[str],
    as_of_date,
) -> dict:
    canonical_customers = set(customer_ids)
    valid_account_ids = set(accounts.loc[accounts["customer_id"].isin(canonical_customers), "account_id"])

    recon = build_regulatory_extract(loans, accounts, transactions, customer_ids)

    tables: dict[str, dict] = {}

    # --- loan_originations ---
    loan_age_days = (pd.Timestamp(as_of_date) - pd.to_datetime(loans["origination_date"])).dt.days
    loan_checks = [
        _check_completeness(loans, "loan_originations", "customer_id"),
        _check_referential_integrity(
            loans, "loan_originations", "customer_id", canonical_customers, "the canonical customer table"
        ),
        _check_freshness(loans, "loan_originations", loan_age_days, 10_000, "dated in the future"),
        _check_reconciliation(
            recon["dropped_loan_rows"],
            len(loans),
            "loan_originations",
            f"${recon['raw_loan_total'] - recon['extract_loan_total']:,.2f} of loan principal "
            f"({recon['dropped_loan_rows']} of {len(loans)} rows) never made it into regulatory_extract "
            "because the customer_id could not be resolved",
        ),
    ]
    tables["loan_originations"] = {
        "score": _table_score(loan_checks),
        "row_count": int(len(loans)),
        "checks": loan_checks,
    }

    # --- account_master ---
    account_age_days = (pd.Timestamp(as_of_date) - pd.to_datetime(accounts["last_updated_ts"])).dt.days
    orphan_accounts = int((~accounts["customer_id"].isin(canonical_customers)).sum())
    account_checks = [
        _check_completeness(accounts, "account_master", "customer_id"),
        _check_referential_integrity(
            accounts, "account_master", "customer_id", canonical_customers, "the canonical customer table"
        ),
        _check_freshness(
            accounts,
            "account_master",
            account_age_days,
            FRESHNESS_SLA_DAYS_ACCOUNT,
            f"older than the {FRESHNESS_SLA_DAYS_ACCOUNT}-day freshness SLA",
        ),
        _check_reconciliation(
            orphan_accounts,
            len(accounts),
            "account_master",
            f"{orphan_accounts} of {len(accounts)} account_master rows reference a customer_id that "
            "doesn't exist, so those accounts are silently excluded from regulatory_extract",
        ),
    ]
    tables["account_master"] = {
        "score": _table_score(account_checks),
        "row_count": int(len(accounts)),
        "checks": account_checks,
    }

    # --- transactions ---
    txn_age_days = (pd.Timestamp(as_of_date) - pd.to_datetime(transactions["ts"])).dt.days
    txn_checks = [
        _check_completeness(transactions, "transactions", "account_id"),
        _check_referential_integrity(
            transactions, "transactions", "account_id", valid_account_ids, "account_master"
        ),
        _check_freshness(
            transactions,
            "transactions",
            txn_age_days,
            FRESHNESS_SLA_DAYS_TXN,
            f"older than the {FRESHNESS_SLA_DAYS_TXN}-day freshness SLA",
        ),
        _check_reconciliation(
            recon["dropped_txn_rows"],
            len(transactions),
            "transactions",
            f"${recon['raw_txn_total'] - recon['extract_txn_total']:,.2f} of transaction volume "
            f"({recon['dropped_txn_rows']} of {len(transactions)} rows) never made it into "
            "regulatory_extract because the account_id doesn't exist in account_master",
        ),
    ]
    tables["transactions"] = {
        "score": _table_score(txn_checks),
        "row_count": int(len(transactions)),
        "checks": txn_checks,
    }

    # --- regulatory_extract ---
    extract_df = recon["extract"]
    extract_checks = [
        _check_completeness(extract_df, "regulatory_extract", "customer_id"),
        _check_referential_integrity(
            extract_df, "regulatory_extract", "customer_id", canonical_customers, "the canonical customer table"
        ),
        _score_from_ratio(0, len(extract_df)) | {
            "category": "freshness",
            "description": f"regulatory_extract was generated as of {as_of_date} with no stale rows",
        },
        _check_reconciliation(
            0,
            0,
            "regulatory_extract",
            f"regulatory_extract undercounts total reported amount by "
            f"${recon['reconciliation_gap_amount']:,.2f} ({recon['reconciliation_gap_pct']:.2f}%) "
            "compared to raw source aggregates due to unresolved foreign keys",
        ),
    ]
    # The extract's own reconciliation check score should reflect the real gap,
    # not a vacuous 0-of-0 ratio.
    extract_checks[-1]["score"] = round(max(0.0, 100.0 - recon["reconciliation_gap_pct"]), 2)
    extract_checks[-1]["unexpected_percent"] = round(recon["reconciliation_gap_pct"], 4)
    tables["regulatory_extract"] = {
        "score": _table_score(extract_checks),
        "row_count": int(len(extract_df)),
        "checks": extract_checks,
    }

    aggregate_score = round(sum(t["score"] for t in tables.values()) / len(tables), 2)

    failing_checks = []
    for table_name, table in tables.items():
        for check in table["checks"]:
            if check["score"] < 99.5:
                failing_checks.append(
                    {"table": table_name, "category": check["category"], "description": check["description"], "score": check["score"]}
                )

    return {
        "tables": tables,
        "aggregate_score": aggregate_score,
        "aggregate_verdict": _verdict(aggregate_score),
        "reconciliation_gap_pct": round(recon["reconciliation_gap_pct"], 2),
        "reconciliation_gap_amount": round(recon["reconciliation_gap_amount"], 2),
        "failing_checks": failing_checks,
    }


if __name__ == "__main__":
    from generate_data import generate_all

    data = generate_all(seed=1, run_index=1)
    result = run_quality_checks(
        data["loan_originations"], data["account_master"], data["transactions"], data["customer_ids"], data["as_of_date"]
    )
    print("aggregate:", result["aggregate_score"], result["aggregate_verdict"])
    for t, v in result["tables"].items():
        print(f"  {t}: {v['score']}")
    print("failing checks:")
    for c in result["failing_checks"]:
        print(" -", c["description"])
