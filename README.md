# Meridian

A local analytics warehouse you can ask questions in English, and a record of
how often it answers correctly.

Synthetic e-commerce data flows through a DuckDB warehouse built with dbt,
where every rejected row is traceable to a rule. A local language model
translates business questions into SQL against a semantic layer, a parser
validates each query before it runs, and a 50-question benchmark measures how
well the whole thing works.

Everything runs on a laptop. No API keys, no cloud, no data leaving the
machine.

---

## Results

| Agent | Accuracy | Mean latency | Dominant failure |
|---|---|---|---|
| qwen2.5-coder:7b + guardrails | **66.0%** | 3.5s | wrong value (9) |
| qwen2.5-coder:3b | 57.4% | 1.7s | wrong value (8) |
| sqlcoder:7b | 31.9% | 14.1s | SQL error (19) |
| hand-written templates | 23.4% | 0.0s | no template matched (22) |

Scored on 47 questions with answers verified against the warehouse. Scoring is
strict: a refusal on an answerable question counts as wrong.

**Verified deterministic.** Three consecutive runs produced byte-identical SQL
for all 50 questions. The numbers above are reproducible, not sampled.

**On questions SQL cannot answer**, a two-stage analyst (fixed retrieval query,
then LLM classification) found 12 of 12 planted rating/text contradictions —
recall 1.000, precision 0.46 raw. Every false positive turned out to be a
review whose appended text genuinely contained a complaint my ground-truth
labels ignored, so the model was reading something the labels were not.

---

## Three things that went wrong, and what they cost

The most useful part of this project was not building the pipeline. It was
finding the bugs that produce no error.

### An outlier in 0.2% of rows inflated revenue by 578%

452 line items out of 228,849 carried quantities between 500 and 9,999 units.
They account for **85.2% of raw revenue** — $741.2M reported against $109.3M
actual.

The silver layer flagged them correctly. Nothing downstream acted on the flag,
so every gold revenue measure was 6.8× too high. A boolean nobody filters on is
decoration.

The fix was to expose `line_total_ex_outliers` alongside `line_total` as named
columns, so the choice is visible in the schema rather than hidden in a
`WHERE` clause someone forgot.

### A join to a table with duplicate keys silently multiplied 218 rows

`stg_order_items` joined to `bronze.orders` to check for orphan order IDs.
`bronze.orders` contains 100 deliberately duplicated order IDs, so every line
item on a duplicated order came back twice.

No error. Row counts just came out wrong, and if you were summing revenue
rather than counting rows you would never notice. One `DISTINCT` fixed it. A
`unique` test on `order_item_id` now catches it.

### Random ID generation collided 226 times

Injected line items were given IDs like `ITEM-9{7 random digits}`. With ~940
draws from a 10-million space, collisions are near-certain — and the second
injection loop sampled from a pool already containing the first loop's clones,
compounding it. The line-item table's primary key was not a key.

Sequential counters have no such failure mode.

---

## What the guardrail actually changed

Shipping the guardrail **cost 8.6 points of accuracy** on the first
measurement: 66.0% → 57.4%.

The cause was mine. I built the column allowlist from the semantic layer's
prose, which documents columns selectively by design. The guardrail was
rejecting valid queries against real columns I had not bothered to write down.

| | no guardrail | broken | fixed |
|---|---|---|---|
| Accuracy | 66.0% | 57.4% | 66.0% |
| SQL errors | 2 | 5 | **1** |
| Easy-tier accuracy | — | 90% | **100%** |

The corrected version sources **which tables are reachable** from the semantic
layer (that is the access contract) and **which columns exist** from
`information_schema` (that is a fact about the database). Conflating the two
caused the regression.

Net effect: the headline is unchanged, SQL errors dropped by half, and two
questions now succeed because the guardrail caught a wrong-table reference and
the retry acted on the hint. The guardrail converts errors into corrections.

It does **not** yet enforce `is_completed`. Required filters are applied on
7 of 14 questions that need them. That gap is real and stated rather than
hidden.

---

## The warehouse

```
data/raw/*.csv, *.jsonl      5 source files, 3 formats
        ↓
bronze.*                     loaded verbatim, every column VARCHAR
        ↓                    nothing cleaned, cast, or deduplicated
silver.stg_*                 types cast, duplicates resolved, JSON flattened
        ↓                    rejected rows routed to quarantine
gold.dim_*, fct_*, obt_*     dimensional models + one denormalised table
```

**15 dbt models, 37 tests**, and exact reconciliation at both grains:

| | in gold | quarantined | source |
|---|---|---|---|
| Orders | 99,500 | 500 | 100,000 ✓ |
| Line items | 228,849 | 6,976 | 235,825 ✓ |

Every rejection carries a reason. The 6,976 quarantined line items break down
into orphan products, null quantities, orphan orders — and 1,131 rows rejected
solely because *their parent order* was quarantined. Rejecting 500 orders for
orphan customer IDs took 1,131 line items with them. That cascade is traceable
because the reason is recorded; without it, those rows would simply be absent
from gold with nothing explaining why.

### Bronze is not cleaned, deliberately

Negative prices stay negative. Orphan IDs stay orphaned. Cleaning bronze
destroys the evidence of what the source actually sent, and when someone asks
why a number differs from the source system, you have no answer.

---

## Why an LLM at all

Most data quality problems are solvable in SQL. One category is not.

Roughly 5% of reviews carry a rating that contradicts their text — five stars
on *"Broke after a week. Very disappointed."* The rating is valid. The text is
non-empty. The foreign keys resolve. **No SQL constraint can express this.**

That gap is the entire justification for the model, and it is measured rather
than asserted: 12 of 12 found, on labelled ground truth.

---

## Governance

Generated SQL passes three independent layers before it reaches data:

1. **The model** is instructed to refuse write operations, and does — usually
   the first and cheapest defence to fire.
2. **A SQLGlot parser** checks the statement type on the AST, validates every
   table against an allowlist and every column against the live schema, and
   blocks raw revenue measures unless the question explicitly asks for them.
3. **The connection is read-only.** A `DELETE` fails at the database layer
   regardless of what the model produced and whether anything else caught it.

Parsing rather than regex matters: a keyword scan rejects a column named
`dataset_id` for containing "set", and misses `DELETE` inside a string literal.

The semantic layer is where the guardrail's rules come from. It records what
each table means, which one answers which question, and that "revenue" has
three defensible definitions — so the default is a documented choice rather
than whichever column the model reached for.

---

## Running it

Requires Python 3.12, and [Ollama](https://ollama.com) for the agent.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python scripts/generate_all.py      # ~7s   build the source data
python scripts/load_bronze.py       # ~10s  load into DuckDB

cd dbt && dbt deps && dbt run && dbt test && cd ..
```

Ask it something:

```bash
ollama pull qwen2.5-coder:7b
ollama serve &

python agent/llm_agent.py "which product category generates the most revenue"
python agent/baseline_agent.py "what is our total revenue"
```

Reproduce the benchmark:

```bash
python eval/run_eval.py --agent baseline
python eval/run_eval.py --agent llm --model qwen2.5-coder:7b
python agent/text_analyst.py --task mismatch --sample 200
```

Browse the results:

```bash
streamlit run app/console.py
```

---

## Layout

```
scripts/     synthetic data generators, bronze loader
dbt/         15 models across bronze → silver → gold + quarantine
semantic/    what the tables mean, in YAML
agent/       baseline templates, LLM agent, guardrails, text analyst
eval/        50-question benchmark, harness, results
app/         Streamlit console
```

**Stack:** Python 3.12 · DuckDB 1.5 · dbt-core 1.12 · Ollama · SQLGlot ·
Streamlit · pandas · Faker

---

## What this does not do

The data is synthetic, so the analytical findings are recovered rather than
discovered — the complaint themes the model surfaces are the phrase banks the
generator planted. The pipeline is demonstrated; the insight is not real.

Required-filter enforcement covers the revenue measure but not order status.
Nine questions still return plausible wrong numbers, mostly grain confusion
and averages of averages. There is no orchestration or CI yet.

66% is not a good enough accuracy for anyone to query a warehouse unsupervised.
It is, however, measured, reproducible, and broken down by failure mode — which
is the part that would let you improve it.
