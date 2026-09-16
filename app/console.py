"""Meridian console.

An observability surface for a governed text-to-SQL system. Four pages:

  Ask         the agent, with the full pipeline visible -- question, generated
              SQL, guardrail verdict, result. A blocked query is shown as
              blocked. The transparency is the point; an answer appearing from
              nowhere would demonstrate nothing.
  Warehouse   what the pipeline rejected and why, plus the reconciliation
              proving nothing was lost.
  Evaluation  benchmark results across four agents, with failures categorised.
  Business    the e-commerce metrics the warehouse exists to answer.

Pages 2-4 read the DuckDB file and work anywhere. Ask needs Ollama running
locally and says so plainly when it is not.

Run:  streamlit run app/console.py
"""

import json
import sys
from pathlib import Path

import altair as alt
import duckdb
import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "agent"))

from theme import CSS, row, sheet, headline, lede, note, INK, MUTED, SIGNAL, ALERT, RULE  # noqa: E402

DB_PATH = ROOT / "meridian.duckdb"
RESULTS_DIR = ROOT / "eval" / "results"

st.set_page_config(
    page_title="Meridian",
    page_icon="◆",
    layout="wide",
    initial_sidebar_state="expanded",
)
st.markdown(CSS, unsafe_allow_html=True)

alt.data_transformers.disable_max_rows()


# ---------------------------------------------------------------------------
# Data access
# ---------------------------------------------------------------------------

@st.cache_resource
def connect():
    return duckdb.connect(str(DB_PATH), read_only=True)


@st.cache_data(ttl=300)
def q(sql: str) -> pd.DataFrame:
    return connect().sql(sql).df()


@st.cache_data(ttl=60)
def load_results() -> dict:
    out = {}
    if not RESULTS_DIR.exists():
        return out
    for f in sorted(RESULTS_DIR.glob("*.json")):
        if f.stem.startswith("variance"):
            continue
        try:
            out[f.stem] = json.loads(f.read_text())
        except json.JSONDecodeError:
            continue
    return out


def ollama_up() -> bool:
    import urllib.request
    try:
        urllib.request.urlopen("http://localhost:11434/api/tags", timeout=1.5)
        return True
    except Exception:
        return False


def chart_base(df, x, y, **kw):
    return alt.Chart(df).properties(height=kw.pop("height", 260))


AXIS = alt.Axis(
    labelFont="IBM Plex Mono", labelFontSize=10, labelColor=MUTED,
    titleFont="IBM Plex Sans", titleFontSize=11, titleColor=MUTED,
    grid=False, tickColor=RULE, domainColor=RULE,
)
AXIS_Y = alt.Axis(
    labelFont="IBM Plex Mono", labelFontSize=10, labelColor=MUTED,
    titleFont="IBM Plex Sans", titleFontSize=11, titleColor=MUTED,
    grid=True, gridColor=RULE, gridDash=[1, 3], domain=False, tickSize=0,
)


# ---------------------------------------------------------------------------
# Ask
# ---------------------------------------------------------------------------

def page_ask() -> None:
    st.markdown("# Ask the warehouse")
    st.markdown(lede(
        "Type a business question. A local language model writes the SQL, a "
        "parser checks it against the schema and the warehouse's own rules, "
        "and only then does it run. Every stage is shown, including the ones "
        "that reject a query."
    ), unsafe_allow_html=True)

    if not ollama_up():
        st.markdown(note(
            "Ollama is not responding on localhost:11434. Start it with "
            "<code>ollama serve</code> in a terminal, then reload this page. "
            "The other three pages read the warehouse file directly and work "
            "without it."
        ), unsafe_allow_html=True)
        return

    col_q, col_m = st.columns([3, 1])
    with col_q:
        question = st.text_input(
            "Question", value="", placeholder="What is our total revenue?",
            label_visibility="collapsed",
        )
    with col_m:
        model = st.selectbox(
            "Model", ["qwen2.5-coder:7b", "qwen2.5-coder:3b", "sqlcoder:7b"],
            label_visibility="collapsed",
        )

    examples = [
        "Which product category generates the most revenue?",
        "How many orders were cancelled last year?",
        "Delete all cancelled orders",
    ]
    cols = st.columns(len(examples))
    for col, ex in zip(cols, examples):
        if col.button(ex, key=f"ex_{ex}", use_container_width=True):
            question = ex

    if not question:
        st.markdown(note(
            "The third example is a write request. It is included deliberately: "
            "the system refuses it at three independent layers, and you can "
            "watch which one fires first."
        ), unsafe_allow_html=True)
        return

    import llm_agent
    from guardrails import Guardrail

    with st.spinner("Generating and validating…"):
        guard = Guardrail()
        ctx = llm_agent.load_schema_context()
        result = llm_agent.run(question, connect(), ctx, guard=guard, model=model)

    steps = []
    steps.append(
        '<div class="step on"><div class="name">Question</div>'
        f'<div class="body">{question}</div></div>'
    )

    if result.sql:
        steps.append(
            '<div class="step on"><div class="name">Generated SQL'
            f'{" — second attempt after correction" if result.retried else ""}</div>'
            f'<div class="sqlbox">{result.sql}</div></div>'
        )
    elif result.refused:
        # The model declined before writing anything. This is the first of
        # three independent defences and the cheapest one -- naming it as a
        # refusal rather than an absence matters, because "produced no query"
        # reads as a failure when it is the system working.
        steps.append(
            '<div class="step on"><div class="name">Generated SQL</div>'
            '<div class="body">The model declined to write a query for this '
            'request.</div></div>'
        )
    else:
        steps.append(
            '<div class="step off"><div class="name">Generated SQL</div>'
            '<div class="body">The model produced no query.</div></div>'
        )

    if result.refused:
        reason = (result.refusal_reason or "").replace("REFUSED:", "").strip()
        steps.append(
            '<div class="step off"><div class="name">Guardrail</div>'
            '<div class="body"><span class="verdict block">BLOCKED</span>'
            f'&nbsp;&nbsp;{reason or "write operations are not permitted"}'
            f'<div style="color:{MUTED};font-size:.82rem;margin-top:.5rem">'
            'Three layers would have caught this independently: the model '
            'declined, the parser rejects any statement that is not a SELECT, '
            'and the database connection is opened read-only.</div>'
            '</div></div>'
        )
    elif result.error:
        steps.append(
            '<div class="step off"><div class="name">Execution</div>'
            f'<div class="body">{result.error[:400]}</div></div>'
        )
    elif result.sql:
        v = guard.check(result.sql, question)
        warns = " · ".join(v.warnings) if v.warnings else ""
        steps.append(
            '<div class="step on"><div class="name">Guardrail</div>'
            '<div class="body"><span class="verdict allow">ALLOWED</span>'
            f'&nbsp;&nbsp;<span style="color:{MUTED};font-size:.85rem">'
            f'{len(v.tables_used)} table(s) checked, '
            f'{len(v.columns_used)} column(s) resolved'
            f'{" · " + warns if warns else ""}</span></div></div>'
        )

    st.markdown('<div class="trace">' + "".join(steps) + "</div>",
                unsafe_allow_html=True)

    if result.rows:
        st.markdown("")
        df = pd.DataFrame(result.rows, columns=result.columns)
        st.dataframe(df, use_container_width=True, hide_index=True,
                     height=min(400, 40 + 35 * min(len(df), 10)))
        st.markdown(
            f'<div style="color:{MUTED};font-size:.8rem;font-family:IBM Plex Mono">'
            f'{len(df):,} row(s) · {result.latency_s:.1f}s</div>',
            unsafe_allow_html=True,
        )


# ---------------------------------------------------------------------------
# Warehouse
# ---------------------------------------------------------------------------

def page_warehouse() -> None:
    st.markdown("# What the pipeline rejected")
    st.markdown(lede(
        "Rows that fail validation are not dropped. They are routed to a "
        "quarantine schema with the reason recorded, so every rejection is "
        "traceable back to a rule and a source file."
    ), unsafe_allow_html=True)

    counts = q("""
        select 'orders' as layer,
               (select count(*) from bronze.orders)        as bronze,
               (select count(*) from gold.fct_orders)      as gold,
               (select count(*) from quarantine.rejected_orders) as quarantined
        union all
        select 'line items',
               (select count(*) from bronze.order_items),
               (select count(*) from gold.fct_order_items),
               (select count(*) from quarantine.rejected_order_items)
    """)

    o = counts.iloc[0]
    st.markdown(headline(
        f"{int(o.quarantined) + int(counts.iloc[1].quarantined):,}",
        "rows quarantined across orders and line items, each with the rule "
        "that rejected it. None were silently discarded."
    ), unsafe_allow_html=True)

    st.markdown("## Reconciliation")
    rows = []
    for _, r in counts.iterrows():
        distinct = q(f"""
            select count(distinct {'order_id' if r.layer == 'orders' else 'order_item_id'})
            from bronze.{'orders' if r.layer == 'orders' else 'order_items'}
        """).iloc[0, 0]
        accounted = int(r.gold) + int(r.quarantined)
        ok = accounted == int(distinct)
        rows.append(row(
            f"{r.layer.title()}",
            f"{accounted:,} / {int(distinct):,}",
            f"{int(r.gold):,} in gold + {int(r.quarantined):,} quarantined",
            "pass" if ok else "fail",
        ))
    st.markdown(sheet(rows), unsafe_allow_html=True)

    st.markdown("## Rejections by rule")
    rej = q("""
        select rejection_reason as reason, count(*) as rows
        from quarantine.rejected_order_items
        group by 1
        union all
        select rejection_reason, count(*)
        from quarantine.rejected_orders
        group by 1
        order by rows desc
    """)
    chart = alt.Chart(rej).mark_bar(size=18, color=ALERT, opacity=.85).encode(
        x=alt.X("rows:Q", axis=AXIS_Y, title="rows rejected"),
        y=alt.Y("reason:N", sort="-x", axis=AXIS, title=None),
        tooltip=["reason", "rows"],
    ).properties(height=26 * len(rej) + 30)
    st.altair_chart(chart, use_container_width=True)

    st.markdown(note(
        "Orphan product references outnumber the planted defect by roughly "
        "three to one. Five products were rejected for negative prices, which "
        "orphaned every line item referencing them — a rejection cascade that "
        "only becomes visible because the reasons are recorded."
    ), unsafe_allow_html=True)

    st.markdown("## Defects that survive into gold")
    d = q("""
        select
            (select count(*) from gold.fct_order_items where is_outlier_quantity) as outliers,
            (select round(sum(line_total), 0) from gold.fct_order_items) as raw_rev,
            (select round(sum(line_total_ex_outliers), 0) from gold.fct_order_items) as clean_rev,
            (select count(*) from gold.fct_orders where is_total_mismatched) as mismatched,
            (select count(*) from gold.dim_products where is_margin_violation) as margin,
            (select count(*) from gold.dim_customers where city is null) as no_city
    """).iloc[0]

    share = (d.raw_rev - d.clean_rev) / d.raw_rev * 100
    total_lines = q("select count(*) from gold.fct_order_items").iloc[0, 0]

    st.markdown(sheet([
        row("Outlier quantity lines",
            f"{int(d.outliers):,}",
            f"{d.outliers / total_lines * 100:.2f}% of rows, carrying "
            f"{share:.0f}% of raw revenue", "fail"),
        row("Header total disagrees with its lines", f"{int(d.mismatched):,}",
            "flagged, not rejected — the order and its money are real", "warn"),
        row("Products costing more than they sell for", f"{int(d.margin):,}",
            "every field valid; only domain knowledge finds it", "warn"),
        row("Customers with no city", f"{int(d.no_city):,}",
            "stored as the literal string 'N/A' upstream, invisible to null checks",
            "warn"),
    ]), unsafe_allow_html=True)

    st.markdown("## Revenue, with and without the outliers")
    comp = pd.DataFrame({
        "measure": ["Including outlier lines", "Excluding outlier lines"],
        "revenue": [float(d.raw_rev), float(d.clean_rev)],
    })
    st.altair_chart(
        alt.Chart(comp).mark_bar(size=34).encode(
            x=alt.X("revenue:Q", axis=AXIS_Y, title="revenue (USD)"),
            y=alt.Y("measure:N", sort=None, axis=AXIS, title=None),
            color=alt.Color("measure:N", legend=None,
                            scale=alt.Scale(range=[ALERT, SIGNAL])),
            tooltip=[alt.Tooltip("revenue:Q", format=",.0f")],
        ).properties(height=110),
        use_container_width=True,
    )


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

def page_eval() -> None:
    st.markdown("# How well it actually works")
    st.markdown(lede(
        "Fifty questions with answers verified against the warehouse, run "
        "against every agent. Scoring is strict: a refusal on an answerable "
        "question counts as wrong. Failure modes are recorded separately, "
        "because two agents can share a score and fail in ways that matter "
        "very differently."
    ), unsafe_allow_html=True)

    results = load_results()
    if not results:
        st.markdown(note("No result files found in eval/results."),
                    unsafe_allow_html=True)
        return

    runs = {k: v for k, v in results.items() if "results" in v}
    if not runs:
        st.markdown(note("No benchmark runs found."), unsafe_allow_html=True)
        return

    summary = []
    for key, payload in runs.items():
        rs = [r for r in payload["results"] if r["failure_mode"] != "manual_review"]
        if not rs:
            continue
        ok = sum(1 for r in rs if r["correct"])
        lat = [r["latency_s"] for r in rs if r.get("latency_s")]
        summary.append({
            "key": key,
            "agent": payload["label"].replace("llm agent ", "").strip("[]"),
            "correct": ok,
            "n": len(rs),
            "accuracy": ok / len(rs) * 100,
            "latency": sum(lat) / len(lat) if lat else 0,
        })
    summary.sort(key=lambda r: -r["accuracy"])

    best = summary[0]
    base = next((s for s in summary if "baseline" in s["agent"]), None)

    if base:
        st.markdown(headline(
            f"{best['accuracy']:.1f}%",
            f"best agent ({best['agent']}), against {base['accuracy']:.1f}% for "
            f"the hand-written template baseline on identical questions"
        ), unsafe_allow_html=True)

    st.markdown("## Agents compared")
    st.markdown(sheet([
        row(s["agent"], f"{s['accuracy']:.1f}%",
            f"{s['correct']}/{s['n']} correct · {s['latency']:.1f}s mean",
            "pass" if s["accuracy"] >= 60 else ("warn" if s["accuracy"] >= 40 else "fail"))
        for s in summary
    ]), unsafe_allow_html=True)

    st.markdown(note(
        "The SQL-specialised model finished last among the three. At equal "
        "size it scored 34 points below the general coding model, while the "
        "3B model beat it by 25 — architecture and training mattered several "
        "times more than parameter count."
    ), unsafe_allow_html=True)

    chosen = st.selectbox(
        "Agent", [s["key"] for s in summary],
        format_func=lambda k: next(s["agent"] for s in summary if s["key"] == k),
    )
    rs = [r for r in runs[chosen]["results"] if r["failure_mode"] != "manual_review"]

    left, right = st.columns(2)

    with left:
        st.markdown("### By question type")
        cat = (pd.DataFrame(rs).groupby("category")
               .agg(correct=("correct", "sum"), n=("correct", "size"))
               .reset_index())
        cat["pct"] = cat.correct / cat.n * 100
        st.altair_chart(
            alt.Chart(cat).mark_bar(size=15).encode(
                x=alt.X("pct:Q", axis=AXIS_Y, title="% correct",
                        scale=alt.Scale(domain=[0, 100])),
                y=alt.Y("category:N", sort="-x", axis=AXIS, title=None),
                color=alt.Color("pct:Q", legend=None,
                                scale=alt.Scale(range=[ALERT, SIGNAL])),
                tooltip=["category", "correct", "n"],
            ).properties(height=22 * len(cat) + 30),
            use_container_width=True,
        )

    with right:
        st.markdown("### How it fails")
        fm = (pd.DataFrame(rs).groupby("failure_mode").size()
              .reset_index(name="n").query("failure_mode != 'ok'"))
        if len(fm):
            st.altair_chart(
                alt.Chart(fm).mark_bar(size=15, color=ALERT, opacity=.8).encode(
                    x=alt.X("n:Q", axis=AXIS_Y, title="questions"),
                    y=alt.Y("failure_mode:N", sort="-x", axis=AXIS, title=None),
                    tooltip=["failure_mode", "n"],
                ).properties(height=22 * len(fm) + 30),
                use_container_width=True,
            )
        st.markdown(note(
            "A wrong value is more dangerous than a refusal. It returns a "
            "plausible number with no error, and that is the one that reaches "
            "a slide deck."
        ), unsafe_allow_html=True)

    st.markdown("## Where SQL cannot reach")
    mm = results.get("text_mismatch")
    if mm:
        m = mm["metrics"]
        st.markdown(sheet([
            row("Planted rating/text contradictions", f"{m['actual_mismatches']}",
                "five-star ratings on negative review text"),
            row("Found by the model", f"{m['true_positives']}",
                "recall " + f"{m['recall']:.3f}", "pass"),
            row("Missed", f"{m['false_negatives']}", "", "pass"),
            row("Flagged but not planted", f"{m['false_positives']}",
                "every one carried appended complaint text the labels ignored",
                "warn"),
        ]), unsafe_allow_html=True)
        st.markdown(note(
            "No SQL constraint detects these: the rating is valid, the text is "
            "non-empty, the keys resolve. Recall was perfect. Raw precision of "
            f"{m['precision']:.2f} understates the result — the false positives "
            "were reviews whose appended text genuinely contains a complaint, "
            "so the model was reading something the ground-truth labels did not."
        ), unsafe_allow_html=True)

    with st.expander("Every question and its result"):
        df = pd.DataFrame([{
            "id": r["id"], "question": r["question"],
            "result": "correct" if r["correct"] else r["failure_mode"],
            "difficulty": r["difficulty"],
            "sql": (r.get("sql") or "")[:120],
        } for r in runs[chosen]["results"]])
        st.dataframe(df, use_container_width=True, hide_index=True, height=420)


# ---------------------------------------------------------------------------
# Business
# ---------------------------------------------------------------------------

def page_business() -> None:
    st.markdown("# The business the warehouse describes")
    st.markdown(lede(
        "Figures use the warehouse default: completed orders, outlier "
        "quantities excluded. Both exclusions are choices, and the alternative "
        "measures are named columns rather than hidden assumptions."
    ), unsafe_allow_html=True)

    k = q("""
        select
            (select round(sum(line_total_ex_outliers), 2)
             from gold.fct_order_items where is_completed)          as revenue,
            (select count(*) from gold.fct_orders)                  as orders,
            (select count(*) from gold.dim_customers)               as customers,
            (select count(*) from gold.dim_customers
             where is_repeat_customer)                              as repeat_c
    """).iloc[0]

    st.markdown(headline(
        f"${k.revenue / 1e6:,.1f}M",
        f"net revenue across {int(k.orders):,} orders from "
        f"{int(k.customers):,} customers"
    ), unsafe_allow_html=True)

    monthly = q("""
        select order_month, round(sum(line_total_ex_outliers), 2) as revenue
        from gold.fct_order_items where is_completed
        group by 1 order by 1
    """)
    st.altair_chart(
        alt.Chart(monthly).mark_area(
            line={"color": INK, "strokeWidth": 1.5},
            color=alt.Gradient(
                gradient="linear",
                stops=[alt.GradientStop(color="#FFFFFF", offset=0),
                       alt.GradientStop(color="#C3CEDC", offset=1)],
                x1=1, x2=1, y1=1, y2=0),
        ).encode(
            x=alt.X("order_month:T", axis=AXIS, title=None),
            y=alt.Y("revenue:Q", axis=AXIS_Y, title="monthly revenue (USD)"),
            tooltip=[alt.Tooltip("order_month:T", title="month"),
                     alt.Tooltip("revenue:Q", format="$,.0f")],
        ).properties(height=250),
        use_container_width=True,
    )

    left, right = st.columns(2)

    with left:
        st.markdown("### Revenue by category")
        cat = q("""
            select coalesce(p.category, 'uncategorised') as category,
                   round(sum(i.line_total_ex_outliers), 2) as revenue
            from gold.fct_order_items i
            join gold.dim_products p on i.product_id = p.product_id
            where i.is_completed group by 1 order by revenue desc
        """)
        st.altair_chart(
            alt.Chart(cat).mark_bar(size=17, color=INK, opacity=.85).encode(
                x=alt.X("revenue:Q", axis=AXIS_Y, title="revenue (USD)"),
                y=alt.Y("category:N", sort="-x", axis=AXIS, title=None),
                tooltip=[alt.Tooltip("revenue:Q", format="$,.0f")],
            ).properties(height=26 * len(cat) + 20),
            use_container_width=True,
        )
        st.markdown(note(
            "Books and Beauty each appear in roughly as many orders as "
            "Electronics and produce a fraction of the revenue. The difference "
            "is price per item, not demand."
        ), unsafe_allow_html=True)

    with right:
        st.markdown("### Order economics")
        e = q("""
            with per_order as (
                select order_id, sum(line_total_ex_outliers) as v
                from gold.fct_order_items where is_completed group by 1
            )
            select round(avg(v), 2) as mean, round(median(v), 2) as median
            from per_order
        """).iloc[0]
        basket = q("""
            select round(avg(n), 2) as items from (
                select order_id, count(*) as n from gold.fct_order_items group by 1
            )
        """).iloc[0, 0]
        st.markdown(sheet([
            row("Mean order value", f"${e['mean']:,.2f}"),
            row("Median order value", f"${e['median']:,.2f}",
                "the mean runs 87% higher — expensive electronics pull it up"),
            row("Items per order", f"{basket:.2f}"),
            row("Repeat customers", f"{int(k.repeat_c):,}",
                f"{k.repeat_c / k.customers * 100:.1f}% have five or more orders"),
        ]), unsafe_allow_html=True)


# ---------------------------------------------------------------------------

PAGES = {
    "Ask": page_ask,
    "Warehouse": page_warehouse,
    "Evaluation": page_eval,
    "Business": page_business,
}


def main() -> None:
    with st.sidebar:
        st.markdown(
            f'<div style="font-family:IBM Plex Mono;font-size:1.5rem;'
            f'letter-spacing:-.02em;color:#FFF;margin-bottom:.1rem">◆ Meridian</div>'
            f'<div style="font-size:.8rem;color:#8C9CAF;line-height:1.45;'
            f'margin-bottom:1.8rem">A warehouse you can ask questions, and a '
            f'record of how often it answers correctly.</div>',
            unsafe_allow_html=True,
        )
        choice = st.radio("Section", list(PAGES), label_visibility="collapsed")

        st.markdown('<div style="height:1.5rem"></div>', unsafe_allow_html=True)
        try:
            n = q("""select (select count(*) from gold.fct_order_items) as a,
                            (select count(*) from silver.stg_events) as b""").iloc[0]
            st.markdown(
                f'<div style="font-size:.76rem;color:#8C9CAF;'
                f'font-family:IBM Plex Mono;line-height:1.8">'
                f'{int(n.a):,} line items<br>{int(n.b):,} events<br>'
                f'{"ollama up" if ollama_up() else "ollama down"}</div>',
                unsafe_allow_html=True,
            )
        except Exception:
            st.markdown(
                '<div style="font-size:.76rem;color:#8C9CAF">'
                'Warehouse not found. Run the generators and dbt first.</div>',
                unsafe_allow_html=True,
            )

    PAGES[choice]()


if __name__ == "__main__":
    main()
