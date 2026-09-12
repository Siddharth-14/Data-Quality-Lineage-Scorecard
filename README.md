# Data Quality & Lineage Scorecard

An always-on alternative to point-in-time compliance audits, inspired publicly by the
Federal Reserve's 2024 penalty against Citigroup for "insufficient progress remediating
data-quality-management deficiencies." This project touches no real institution's systems,
data, or personnel — it's a from-scratch, 100% synthetic demo.

**Live demo:** _add your Netlify URL here after deploying_

**100% synthetic data.** No real financial institution's data, systems, or personnel are used
or referenced anywhere in this repo.

## What it does

Three synthetic "banking systems" (loan origination, core banking, transaction processing)
feed one regulatory extract. A pandas pipeline injects realistic data-quality defects (missing
keys, orphaned foreign keys, stale records), a real Great Expectations suite scores each table
on completeness / referential integrity / freshness / reconciliation, and the transform emits
genuine OpenLineage RunEvent JSON. The pipeline runs 5 times to simulate 5 days of continuous
monitoring — the dashboard lets you step through them to watch the scorecard (and a documented
PASS / AT RISK / FAIL verdict) move over time.

## Regenerating the data

```bash
cd data-pipeline
pip install -r requirements.txt
python run_all.py   # writes site/data/run_1.json .. run_5.json
```

## Deploying

Point Netlify at this repo with publish directory `site` and no build command — the 5 run
JSON files are pre-generated and already committed, so the deployed link works instantly.
