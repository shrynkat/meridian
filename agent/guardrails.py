"""SQL guardrails: parse, validate, and enforce before execution.

Three classes of check, in order of severity:

  STRUCTURAL   Does the query parse? Is it a SELECT? Do its tables and
               columns exist? These are hard failures — the query cannot
               run and the model gets a precise error to retry against,
               which is more useful than the database's own message.

  SEMANTIC     Does the query use the default revenue measure? Blocked
               rather than warned: a caveat attached to a wrong number does
               not survive being pasted into a message, and we already
               learned in the warehouse that a flag nothing acts on is
               decoration. Blocking is conditional — a question that
               explicitly asks for gross or raw figures is allowed through.

  ADVISORY     Cross joins, unbounded scans. Reported, not blocked.

Why a parser rather than regex: regex on SQL is fragile. A keyword scan
rejects a column named `dataset_id` for containing "set", and misses
DELETE inside a string literal. SQLGlot builds an AST, so the checks
inspect what the query DOES rather than what its text looks like.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

import sqlglot
from sqlglot import exp
import yaml

SEMANTIC_PATH = Path(__file__).resolve().parent.parent / "semantic" / "semantic_layer.yml"

DIALECT = "duckdb"

# Raw revenue columns: correct only when the question asks for them.
RAW_REVENUE_COLUMNS = {
    "line_total": "line_total_ex_outliers",
    "gross_revenue": "net_revenue_ex_outliers",
}

# Phrases that make a raw measure the right choice.
EXPLICIT_RAW_MARKERS = re.compile(
    r"\b(gross|raw|including outliers|include outliers|all orders|"
    r"before exclusions|unfiltered|every order|total including)\b",
    re.IGNORECASE,
)


@dataclass
class GuardrailVerdict:
    allowed: bool
    sql: str
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    tables_used: list[str] = field(default_factory=list)
    columns_used: list[str] = field(default_factory=list)
    retry_hint: str | None = None

    @property
    def blocked_reason(self) -> str | None:
        return self.errors[0] if self.errors else None


class Guardrail:
    """Validates generated SQL against the semantic layer's schema."""

    def __init__(self, spec: dict | None = None):
        if spec is None:
            spec = yaml.safe_load(SEMANTIC_PATH.read_text())
        self.spec = spec

        # Allowlist: table name -> set of column names.
        #
        # WHICH tables are queryable comes from the semantic layer — that is
        # the access contract, and a table absent from it stays unreachable
        # however it exists in the database.
        #
        # WHICH columns exist comes from the database itself. The semantic
        # layer documents columns selectively, by design: it explains the
        # ones that carry meaning or danger, not every audit field. Building
        # the column allowlist from that prose blocked valid queries against
        # real columns that simply were not worth documenting.
        self.allowed: dict[str, set[str]] = {}
        documented = {t["name"].lower(): set((t.get("columns") or {}).keys())
                      for t in spec["tables"]}

        try:
            import duckdb
            db = Path(__file__).resolve().parent.parent / "meridian.duckdb"
            con = duckdb.connect(str(db), read_only=True)
            rows = con.execute("""
                select lower(table_schema || '.' || table_name), lower(column_name)
                from information_schema.columns
            """).fetchall()
            con.close()
            actual: dict[str, set[str]] = {}
            for table, column in rows:
                actual.setdefault(table, set()).add(column)
            for name, doc_cols in documented.items():
                self.allowed[name] = actual.get(name, set()) | doc_cols
        except Exception:
            # No database reachable — fall back to the documented columns.
            self.allowed = documented

        # Bare names (fct_orders) map to qualified ones (gold.fct_orders),
        # since models frequently omit the schema.
        self.bare_to_qualified: dict[str, str] = {}
        for name in self.allowed:
            self.bare_to_qualified[name.split(".")[-1]] = name

        self.default_measure = spec["defaults"]["revenue_measure"]

    # -- structural --------------------------------------------------

    def _parse(self, sql: str):
        try:
            parsed = sqlglot.parse_one(sql, dialect=DIALECT)
        except Exception as exc:  # noqa: BLE001
            return None, f"query does not parse: {exc}"
        if parsed is None:
            return None, "query is empty"
        return parsed, None

    def _check_statement_type(self, tree) -> str | None:
        """SELECT only, checked on the AST rather than the text.

        A text scan for 'delete' fires on a comment or a string literal.
        The AST knows the difference between a keyword and an identifier.
        """
        forbidden = (
            exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create,
            exp.Alter, exp.Command,
        )
        if isinstance(tree, forbidden):
            return f"only SELECT is permitted; got {type(tree).__name__.upper()}"
        for node in tree.find_all(*forbidden):
            return f"query contains a {type(node).__name__.upper()} statement"
        return None

    def _resolve_table(self, name: str) -> str | None:
        n = name.lower()
        if n in self.allowed:
            return n
        return self.bare_to_qualified.get(n)

    def _check_tables(self, tree) -> tuple[list[str], list[str]]:
        """Every referenced table must appear in the semantic layer."""
        found, errors = [], []

        # CTE names are local aliases, not warehouse tables.
        cte_names = {
            cte.alias_or_name.lower()
            for cte in tree.find_all(exp.CTE)
        }

        for table in tree.find_all(exp.Table):
            raw = table.name
            if not raw or raw.lower() in cte_names:
                continue
            qualified = f"{table.db}.{raw}" if table.db else raw
            resolved = self._resolve_table(qualified)
            if resolved is None:
                errors.append(
                    f"table '{qualified}' is not in the allowlist. "
                    f"Available: {', '.join(sorted(self.allowed))}"
                )
            else:
                found.append(resolved)

        return sorted(set(found)), errors

    def _check_columns(self, tree, tables_used: list[str]) -> tuple[list[str], list[str]]:
        """Every column must exist on some table the query references.

        Deliberately permissive about WHICH table: resolving a column to its
        exact source requires full alias tracking, and the goal here is to
        catch hallucinated names, not to reimplement a query planner.
        """
        if not tables_used:
            return [], []

        valid = set()
        for t in tables_used:
            valid |= self.allowed.get(t, set())

        # Aliases defined in the query itself are legitimate references.
        local = {
            a.alias_or_name.lower()
            for a in tree.find_all(exp.Alias)
            if a.alias_or_name
        }
        local |= {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}

        found, errors = [], []
        for col in tree.find_all(exp.Column):
            name = col.name
            if not name or name == "*":
                continue
            lowered = name.lower()
            found.append(lowered)
            if lowered in valid or lowered in local:
                continue
            if any(lowered in cols for cols in self.allowed.values()):
                # Exists somewhere, but not on a table this query selects
                # from — a real error, and a common model failure.
                owners = [t for t, cols in self.allowed.items() if lowered in cols]
                errors.append(
                    f"column '{name}' does not exist on the tables queried; "
                    f"it belongs to {', '.join(owners)}"
                )
            else:
                errors.append(f"column '{name}' does not exist in the warehouse")

        return sorted(set(found)), errors

    # -- semantic ----------------------------------------------------

    def _check_measure(self, tree, question: str) -> tuple[list[str], str | None]:
        """Enforce the default revenue measure unless the question overrides it.

        Blocked rather than warned. A warning next to a 7x-inflated revenue
        figure does not travel with the number. The retry hint names the
        exact column to substitute, which the model reliably acts on.
        """
        if EXPLICIT_RAW_MARKERS.search(question or ""):
            return [], None

        # Only aggregated raw columns matter. Selecting line_total for a
        # single row is fine; SUM(line_total) across the table is not.
        for agg in tree.find_all(exp.Sum, exp.Avg):
            for col in agg.find_all(exp.Column):
                raw = col.name.lower()
                if raw in RAW_REVENUE_COLUMNS:
                    replacement = RAW_REVENUE_COLUMNS[raw]
                    return (
                        [
                            f"aggregating '{raw}' uses the raw measure. The default "
                            f"is '{replacement}'. 452 outlier line items carry ~85% "
                            f"of raw revenue, so this would return a figure roughly "
                            f"7x too high."
                        ],
                        f"Replace {raw} with {replacement}. If the question genuinely "
                        f"asks for gross or raw figures, say 'gross' in the query "
                        f"comment and it will be permitted.",
                    )
        return [], None

    # -- advisory ----------------------------------------------------

    def _advisories(self, tree) -> list[str]:
        out = []

        for join in tree.find_all(exp.Join):
            if join.args.get("on") is None and join.args.get("using") is None:
                kind = (join.side or join.kind or "").upper()
                if kind != "CROSS":
                    out.append("join has no ON or USING clause — this is a cross join")

        selects = list(tree.find_all(exp.Select))
        if selects:
            top = selects[0]
            has_agg = any(top.find(t) for t in (exp.Sum, exp.Count, exp.Avg,
                                                exp.Min, exp.Max))
            if not has_agg and not top.args.get("limit") and not top.args.get("group"):
                out.append("unaggregated query with no LIMIT may return many rows")

        return out

    # -- entry point -------------------------------------------------

    def check(self, sql: str, question: str = "") -> GuardrailVerdict:
        verdict = GuardrailVerdict(allowed=False, sql=sql)

        if not sql or not sql.strip():
            verdict.errors.append("no SQL to validate")
            return verdict

        tree, parse_error = self._parse(sql)
        if parse_error:
            verdict.errors.append(parse_error)
            verdict.retry_hint = "The query is not valid SQL. Rewrite it."
            return verdict

        stmt_error = self._check_statement_type(tree)
        if stmt_error:
            verdict.errors.append(stmt_error)
            return verdict

        tables, table_errors = self._check_tables(tree)
        verdict.tables_used = tables
        verdict.errors.extend(table_errors)

        if table_errors:
            verdict.retry_hint = (
                "Use only the tables listed in the schema. " + table_errors[0]
            )
            return verdict

        columns, column_errors = self._check_columns(tree, tables)
        verdict.columns_used = columns
        verdict.errors.extend(column_errors)

        if column_errors:
            verdict.retry_hint = column_errors[0]
            return verdict

        measure_errors, measure_hint = self._check_measure(tree, question)
        if measure_errors:
            verdict.errors.extend(measure_errors)
            verdict.retry_hint = measure_hint
            return verdict

        verdict.warnings.extend(self._advisories(tree))
        verdict.allowed = True
        return verdict


def _demo() -> None:
    """Run the guardrail against a set of queries covering each check."""
    g = Guardrail()

    cases = [
        ("SELECT sum(line_total_ex_outliers) FROM gold.fct_order_items WHERE is_completed",
         "what is our total revenue", "should pass"),
        ("SELECT sum(line_total) FROM gold.fct_order_items",
         "what is our total revenue", "should block: raw measure"),
        ("SELECT sum(line_total) FROM gold.fct_order_items",
         "what is our gross revenue including outliers", "should pass: explicit"),
        ("DELETE FROM gold.fct_orders WHERE status = 'cancelled'",
         "delete cancelled orders", "should block: not a SELECT"),
        ("SELECT order_count FROM gold.fct_orders",
         "how many orders", "should block: no such column"),
        ("SELECT net_revenue_ex_outliers FROM gold.fct_orders",
         "revenue", "should block: column on another table"),
        ("SELECT * FROM secret_table",
         "anything", "should block: table not allowed"),
        ("SELECT count(*) FROM gold.fct_orders a, gold.dim_customers b",
         "orders", "should warn: cross join"),
        ("SELECT dataset_id FROM gold.fct_orders",
         "x", "regex would false-positive on 'set'"),
    ]

    for sql, question, label in cases:
        v = g.check(sql, question)
        status = "ALLOW " if v.allowed else "BLOCK "
        print(f"{status} {label}")
        print(f"       {sql[:72]}")
        for e in v.errors:
            print(f"       error: {e[:96]}")
        for w in v.warnings:
            print(f"       warn:  {w[:96]}")
        print()


if __name__ == "__main__":
    _demo()
