"""Builds the regulatory_extract table by joining the three source systems.

The extract is a per-customer rollup of loan principal and transaction
amounts. Because loan_originations and transactions carry injected defects
(missing customer_id, orphaned account_id), rows that can't be resolved to a
valid customer_id are silently dropped from the join -- exactly what would
happen with a naive downstream extract job. That silent drop is what creates
the reconciliation gap between the extract and the raw source totals.
"""

from __future__ import annotations

import pandas as pd


def build_regulatory_extract(
    loans: pd.DataFrame,
    accounts: pd.DataFrame,
    transactions: pd.DataFrame,
    customer_ids: list[str],
) -> dict:
    canonical_customers = set(customer_ids)

    # Only rows with a customer_id that resolves to the canonical customer
    # table can be joined into the extract.
    valid_loans = loans[loans["customer_id"].isin(canonical_customers)]
    valid_accounts = accounts[accounts["customer_id"].isin(canonical_customers)]
    valid_account_ids = set(valid_accounts["account_id"])
    valid_transactions = transactions[transactions["account_id"].isin(valid_account_ids)]

    # Map account_id -> customer_id so transaction amounts roll up by customer.
    account_to_customer = valid_accounts.set_index("account_id")["customer_id"]
    txn_with_customer = valid_transactions.assign(
        customer_id=valid_transactions["account_id"].map(account_to_customer)
    )

    loan_totals = valid_loans.groupby("customer_id")["principal"].sum()
    txn_totals = txn_with_customer.groupby("customer_id")["amount"].sum()

    extract = pd.DataFrame({"loan_principal_total": loan_totals}).join(
        pd.DataFrame({"transaction_amount_total": txn_totals}), how="outer"
    ).fillna(0.0)
    extract.index.name = "customer_id"
    extract = extract.reset_index()
    extract["reported_total"] = (
        extract["loan_principal_total"] + extract["transaction_amount_total"]
    )

    extract_loan_total = float(valid_loans["principal"].sum())
    extract_txn_total = float(txn_with_customer["amount"].sum())
    raw_loan_total = float(loans["principal"].sum())
    raw_txn_total = float(transactions["amount"].sum())

    raw_total = raw_loan_total + raw_txn_total
    extract_total = extract_loan_total + extract_txn_total
    gap_amount = raw_total - extract_total
    gap_pct = (gap_amount / raw_total * 100.0) if raw_total else 0.0

    return {
        "extract": extract,
        "raw_loan_total": raw_loan_total,
        "raw_txn_total": raw_txn_total,
        "extract_loan_total": extract_loan_total,
        "extract_txn_total": extract_txn_total,
        "raw_total": raw_total,
        "extract_total": extract_total,
        "reconciliation_gap_amount": gap_amount,
        "reconciliation_gap_pct": gap_pct,
        "dropped_loan_rows": int(len(loans) - len(valid_loans)),
        "dropped_txn_rows": int(len(transactions) - len(valid_transactions)),
    }


if __name__ == "__main__":
    from generate_data import generate_all

    data = generate_all(seed=1, run_index=1)
    result = build_regulatory_extract(
        data["loan_originations"], data["account_master"], data["transactions"], data["customer_ids"]
    )
    print("extract rows:", len(result["extract"]))
    print("reconciliation gap: %.2f%% ($%.2f)" % (result["reconciliation_gap_pct"], result["reconciliation_gap_amount"]))
    print("dropped loan rows:", result["dropped_loan_rows"], "dropped txn rows:", result["dropped_txn_rows"])
