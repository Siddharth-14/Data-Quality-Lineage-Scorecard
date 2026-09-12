"""Synthetic banking data generator for the Data Quality & Lineage Scorecard.

Produces three synthetic "source system" tables that feed a downstream
regulatory extract:
  - loan_originations  (Loan Origination System)
  - account_master     (Core Banking System)
  - transactions       (Transaction Processing System)

All data is 100% synthetic (Faker-generated) and seeded for reproducibility.
Defect rates are intentionally injected so downstream quality checks have
something real to catch. Defect *severity* per run is controlled by
DEFECT_MULTIPLIERS so the 5 simulated runs show a non-monotonic remediation
story (things improve, then partially regress, then improve again) rather
than a suspiciously straight line.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
from faker import Faker

NUM_CUSTOMERS = 1200
NUM_LOANS = 1500
NUM_ACCOUNTS = 1800
NUM_TRANSACTIONS = 6000

BRANCH_SYSTEM_IDS = [f"BR-{i:03d}" for i in range(1, 13)]
PRODUCT_TYPES = ["mortgage", "auto", "personal", "student", "commercial"]
ACCOUNT_STATUSES = ["active", "dormant", "closed"]

BASE_RATES = {
    "loan_missing_customer_id": 0.10,
    "loan_future_dated": 0.06,
    "account_stale": 0.14,
    "account_orphan_customer": 0.10,
    "txn_orphan_account": 0.16,
}

# One multiplier per simulated run (run_1 .. run_5). Tells a "continuous
# monitoring" story: an initial bad state, real improvement, a partial
# regression, then recovery -- the kind of non-linear pattern a once-a-year
# audit would miss but always-on monitoring catches immediately.
DEFECT_MULTIPLIERS = [1.6, 1.15, 0.45, 0.85, 0.3]

FRESHNESS_SLA_DAYS = 180


def _run_multiplier(run_index: int) -> float:
    return DEFECT_MULTIPLIERS[(run_index - 1) % len(DEFECT_MULTIPLIERS)]


def generate_all(seed: int, run_index: int) -> dict:
    """Generate all three source tables plus shared run metadata.

    Returns a dict with keys: as_of_date, customer_ids, loan_originations,
    account_master, transactions (the latter three as pandas DataFrames).
    """
    rng = np.random.default_rng(seed)
    fake = Faker()
    fake.seed_instance(seed)

    as_of_date = dt.date(2025, 1, 1) + dt.timedelta(days=(run_index - 1) * 7)
    mult = _run_multiplier(run_index)

    customer_ids = [f"CUST-{i:06d}" for i in range(1, NUM_CUSTOMERS + 1)]

    loans = _generate_loan_originations(rng, fake, customer_ids, as_of_date, mult)
    accounts = _generate_account_master(rng, fake, customer_ids, as_of_date, mult)
    transactions = _generate_transactions(rng, fake, accounts, as_of_date, mult)

    return {
        "as_of_date": as_of_date,
        "customer_ids": customer_ids,
        "loan_originations": loans,
        "account_master": accounts,
        "transactions": transactions,
    }


def _generate_loan_originations(rng, fake, customer_ids, as_of_date, mult) -> pd.DataFrame:
    n = NUM_LOANS
    customer_choices = rng.choice(customer_ids, size=n)
    origination_days_ago = rng.integers(1, 730, size=n)
    origination_dates = [as_of_date - dt.timedelta(days=int(d)) for d in origination_days_ago]
    principals = np.round(rng.uniform(2000, 500000, size=n), 2)
    product_types = rng.choice(PRODUCT_TYPES, size=n)
    branch_system_ids = rng.choice(BRANCH_SYSTEM_IDS, size=n)

    df = pd.DataFrame(
        {
            "loan_id": [f"LOAN-{i:06d}" for i in range(1, n + 1)],
            "customer_id": customer_choices,
            "origination_date": origination_dates,
            "principal": principals,
            "product_type": product_types,
            "branch_system_id": branch_system_ids,
        }
    )

    missing_rate = min(BASE_RATES["loan_missing_customer_id"] * mult, 0.35)
    missing_mask = rng.random(n) < missing_rate
    df.loc[missing_mask, "customer_id"] = None

    future_rate = min(BASE_RATES["loan_future_dated"] * mult, 0.25)
    future_mask = rng.random(n) < future_rate
    future_days = rng.integers(1, 60, size=n)
    df.loc[future_mask, "origination_date"] = [
        as_of_date + dt.timedelta(days=int(d)) for d in future_days[future_mask]
    ]

    return df


def _generate_account_master(rng, fake, customer_ids, as_of_date, mult) -> pd.DataFrame:
    n = NUM_ACCOUNTS
    customer_choices = rng.choice(customer_ids, size=n)
    opened_days_ago = rng.integers(30, 3650, size=n)
    opened_dates = [as_of_date - dt.timedelta(days=int(d)) for d in opened_days_ago]
    statuses = rng.choice(ACCOUNT_STATUSES, size=n, p=[0.75, 0.15, 0.10])
    fresh_days_ago = rng.integers(0, 30, size=n)
    last_updated = [
        dt.datetime.combine(as_of_date, dt.time(12, 0)) - dt.timedelta(days=int(d))
        for d in fresh_days_ago
    ]

    df = pd.DataFrame(
        {
            "account_id": [f"ACC-{i:06d}" for i in range(1, n + 1)],
            "customer_id": customer_choices,
            "opened_date": opened_dates,
            "status": statuses,
            "last_updated_ts": last_updated,
        }
    )

    stale_rate = min(BASE_RATES["account_stale"] * mult, 0.35)
    stale_mask = rng.random(n) < stale_rate
    stale_days = rng.integers(FRESHNESS_SLA_DAYS + 1, FRESHNESS_SLA_DAYS + 400, size=n)
    stale_ts = [
        dt.datetime.combine(as_of_date, dt.time(12, 0)) - dt.timedelta(days=int(d))
        for d in stale_days
    ]
    df.loc[stale_mask, "last_updated_ts"] = pd.Series(stale_ts, index=df.index)[stale_mask]

    orphan_rate = min(BASE_RATES["account_orphan_customer"] * mult, 0.25)
    orphan_mask = rng.random(n) < orphan_rate
    n_orphans = int(orphan_mask.sum())
    if n_orphans:
        orphan_ids = [f"CUST-UNKNOWN-{i:04d}" for i in range(1, n_orphans + 1)]
        df.loc[orphan_mask, "customer_id"] = orphan_ids

    return df


def _generate_transactions(rng, fake, accounts: pd.DataFrame, as_of_date, mult) -> pd.DataFrame:
    n = NUM_TRANSACTIONS
    account_ids = accounts["account_id"].tolist()
    account_choices = rng.choice(account_ids, size=n)
    amounts = np.round(rng.uniform(5, 25000, size=n) * rng.choice([1, -1], size=n, p=[0.7, 0.3]), 2)
    tx_days_ago = rng.integers(0, 90, size=n)
    timestamps = [
        dt.datetime.combine(as_of_date, dt.time(12, 0)) - dt.timedelta(days=int(d), hours=int(h))
        for d, h in zip(tx_days_ago, rng.integers(0, 24, size=n))
    ]
    systems_of_record = rng.choice(
        ["TPS-CORE", "TPS-CARD", "TPS-WIRE"], size=n, p=[0.6, 0.3, 0.1]
    )

    df = pd.DataFrame(
        {
            "transaction_id": [f"TXN-{i:06d}" for i in range(1, n + 1)],
            "account_id": account_choices,
            "amount": amounts,
            "ts": timestamps,
            "system_of_record": systems_of_record,
        }
    )

    orphan_rate = min(BASE_RATES["txn_orphan_account"] * mult, 0.25)
    orphan_mask = rng.random(n) < orphan_rate
    n_orphans = int(orphan_mask.sum())
    if n_orphans:
        orphan_ids = [f"ACC-UNKNOWN-{i:04d}" for i in range(1, n_orphans + 1)]
        df.loc[orphan_mask, "account_id"] = orphan_ids

    return df


if __name__ == "__main__":
    import sys

    seed = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    run_index = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    data = generate_all(seed, run_index)
    for key in ("loan_originations", "account_master", "transactions"):
        print(key, data[key].shape)
