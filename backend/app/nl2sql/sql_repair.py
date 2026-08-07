"""Deterministic correctness repairs for LLM-generated SQL.

Separate from ``sql_validator``, which is a *safety* gate and rejects; this
module *rewrites*, and only ever makes a query answer the question it was
already trying to answer.

## Why this exists

The accuracy eval (issue #16) measured grouped questions at 30%. The dominant
failure was the model dropping the grouping key from the projection::

    -- "Show the total revenue by region"
    SELECT SUM(total_amount) FROM sales_data GROUP BY region

DuckDB runs it happily and the user gets a column of unlabelled numbers with no
way to tell which region each belongs to. The prompt has forbidden exactly this
in capitals since before the eval existed (``prompt_builder`` rule 8) and a 3B
model does it anyway; adding few-shot examples moved the category from 30% to
44% and only for the table the examples were written against. Prompting is not a
control. This is: it holds for every table, phrasing and model.

## Parsing

Uses DuckDB's own ``json_serialize_sql`` / ``json_deserialize_sql`` rather than
a regex or a new dependency. The parser doing the analysis is the one that will
execute the query, so it cannot disagree about what the SQL means.

The serialized AST is a DuckDB internal format that may shift between versions,
so every step is defensive: **anything unexpected returns the original SQL
unchanged.** A repair that cannot be made confidently is not made. The unit
tests pin the behaviour against whichever DuckDB version CI installs.
"""
from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger("datawhisper.sql_repair")

_SELECT_NODE = "SELECT_NODE"
_COLUMN_REF = "COLUMN_REF"
_STAR = "STAR"
_DISTINCT_MODIFIER = {"type": "DISTINCT_MODIFIER", "distinct_on_targets": []}


def _column_key(node: dict) -> tuple[str, ...] | None:
    """Identity of a COLUMN_REF, qualifier included, case-folded."""
    if node.get("class") != _COLUMN_REF:
        return None
    names = node.get("column_names")
    if not names or not isinstance(names, list):
        return None
    return tuple(str(part).casefold() for part in names)


def _projects_everything(select_list: list) -> bool:
    """True if the projection already includes the grouping key implicitly."""
    return any(item.get("class") == _STAR for item in select_list)


def add_missing_group_keys(sql: str, conn) -> str:
    """Return ``sql`` with any GROUP BY key missing from SELECT added to it.

    Returns the input unchanged when the query is already correct, when the
    shape is anything other than a plain grouped SELECT, or when the AST cannot
    be read. Never raises.
    """
    try:
        # The ::VARCHAR cast is required: these functions reject an untyped bind
        # parameter ("first argument must be a VARCHAR"). Casting keeps the SQL
        # bound rather than interpolated into the statement text.
        serialized = conn.execute("SELECT json_serialize_sql(?::VARCHAR)", [sql]).fetchone()[0]
        payload = json.loads(serialized)
    except Exception as exc:  # noqa: BLE001 — repair is best-effort by design
        logger.debug("group-key repair: could not parse SQL (%s)", exc)
        return sql

    if payload.get("error"):
        return sql
    statements = payload.get("statements") or []
    if len(statements) != 1:
        return sql
    node = statements[0].get("node") or {}

    if node.get("type") != _SELECT_NODE:
        return sql  # set operation, VALUES, …
    # A CTE is deliberately *not* excluded. Only the outermost node's projection
    # is touched, and a "WITH … SELECT SUM(x) FROM t GROUP BY region" is exactly
    # as unreadable as the same query without the CTE.
    if node.get("aggregate_handling") not in (None, "STANDARD_HANDLING"):
        return sql  # GROUP BY ALL and friends already project correctly

    group_expressions = node.get("group_expressions") or []
    if not group_expressions:
        return sql

    # A plain GROUP BY serialises to exactly one grouping set covering every
    # key: GROUP BY x, y -> [[0, 1]]. ROLLUP, CUBE and GROUPING SETS produce
    # several sets (or a partial one), and there the projection is intentional —
    # rows in different sets have different keys populated, so injecting a
    # column would change what the query means rather than label it.
    group_sets = node.get("group_sets")
    if group_sets != [list(range(len(group_expressions)))]:
        return sql

    select_list = node.get("select_list") or []
    if not select_list or _projects_everything(select_list):
        return sql

    # Only plain column references are safe to copy into the projection. An
    # expression key (date_trunc('month', d)) would need an alias to be useful
    # and is rare enough not to guess at.
    group_keys = [_column_key(expr) for expr in group_expressions]
    if any(key is None for key in group_keys):
        return sql

    projected = {key for key in (_column_key(item) for item in select_list) if key is not None}

    missing = [
        expr for expr, key in zip(group_expressions, group_keys, strict=True)
        if key not in projected
    ]
    if not missing:
        return sql

    # Dimension first, then the measures the model already selected — the order
    # a reader expects, and the order the reference answers use.
    node["select_list"] = [json.loads(json.dumps(expr)) for expr in missing] + select_list

    try:
        repaired = conn.execute(
            "SELECT json_deserialize_sql(?::JSON)", [json.dumps(payload)]
        ).fetchone()[0]
    except Exception as exc:  # noqa: BLE001
        logger.debug("group-key repair: could not rebuild SQL (%s)", exc)
        return sql

    if not repaired or not isinstance(repaired, str):
        return sql

    # The rewrite came from DuckDB's own serializer over an already-validated
    # AST, so it cannot introduce anything new — but re-checking costs nothing
    # and keeps the safety gate authoritative.
    from app.nl2sql.sql_validator import is_safe_sql

    if not is_safe_sql(repaired):
        return sql

    logger.info("Repaired missing GROUP BY key(s) in generated SQL")
    return repaired


# ── DISTINCT for value-listing questions (issue #58) ──────────────────────────
#
# "List all the regions" produced `SELECT region FROM sales_data` — 25 rows, 21
# of them duplicates, where 4 were wanted. Worse than a wrong number, because
# every value shown is real and nothing signals the repetition; at upload scale
# it is 100k rows of near-duplicates that a human would not notice.
#
# The prompt route was tried first and measured (see PROJECT_STATUS.md): a rule
# telling the model to use DISTINCT for "list the X" fixed this case and broke
# `sales_product_variety` ("how many different products" -> COUNT(DISTINCT p)),
# leaving the category exactly where it started. The #52 finding again — on a 3B
# model a rule is a suggestion, and it perturbs neighbours.
#
# This trigger is softer than #52's, which was pure AST: it has to read the
# question, because `SELECT region FROM t` is *correct* for "show me every
# order's region" and wrong only for "which regions exist". So the AST guard is
# deliberately narrow and the question cues are explicit, and anything outside
# both is left alone.

# Asking which values exist. Kept to phrasings that are unambiguous about
# wanting the set — "list/show the Xs", "what Xs are there", or an explicit
# unique/distinct/different.
_LISTING_CUES = (
    re.compile(r"\b(?:list|show|display|give)\b(?:\s+me)?\s+(?:all\s+|of\s+)*\s*the\b"),
    re.compile(r"\bwhat\s+\w+\s+are\s+there\b"),
    re.compile(r"\b(?:unique|distinct|different)\b"),
)

# Cues that mean the question is *not* asking for a bare set of values.
# `distribution`/`spread`/`histogram` matter most: those genuinely want every
# raw value (prompt rule 9), and deduplicating them would destroy the answer.
_NOT_LISTING_CUES = re.compile(
    r"\b(?:how\s+many|how\s+much|count|total|sum|average|mean|median|"
    r"distribution|spread|histogram|each|per)\b"
)


def _asks_for_distinct_values(question: str) -> bool:
    q = question.casefold()
    if _NOT_LISTING_CUES.search(q):
        return False
    return any(cue.search(q) for cue in _LISTING_CUES)


def add_distinct_for_value_listing(sql: str, question: str, conn) -> str:
    """Add DISTINCT when the question asked which values exist (issue #58).

    Fires only on the exact shape that is wrong: a bare single-column projection
    off one table, with no DISTINCT, no aggregate, no GROUP BY, no ORDER BY and
    no LIMIT. Returns the input unchanged for anything else. Never raises.
    """
    if not _asks_for_distinct_values(question):
        return sql

    try:
        serialized = conn.execute("SELECT json_serialize_sql(?::VARCHAR)", [sql]).fetchone()[0]
        payload = json.loads(serialized)
    except Exception as exc:  # noqa: BLE001 — repair is best-effort by design
        logger.debug("distinct repair: could not parse SQL (%s)", exc)
        return sql

    if payload.get("error"):
        return sql
    statements = payload.get("statements") or []
    if len(statements) != 1:
        return sql
    node = statements[0].get("node") or {}

    if node.get("type") != _SELECT_NODE:
        return sql
    # DISTINCT, ORDER BY and LIMIT are all modifiers, so an empty list is one
    # check for "not already deduplicated, not ranked, not truncated". A ranked
    # or limited query is answering a different question and must not be touched.
    if node.get("modifiers"):
        return sql
    if node.get("group_expressions") or node.get("having"):
        return sql
    if node.get("aggregate_handling") not in (None, "STANDARD_HANDLING"):
        return sql
    if node.get("qualify") or node.get("sample"):
        return sql

    # Exactly one plain column. Two columns may be a genuine pairing, and a
    # FUNCTION is an aggregate — which is what keeps COUNT(DISTINCT x) (the case
    # the prompt rule broke) out of reach here.
    select_list = node.get("select_list") or []
    if len(select_list) != 1 or _column_key(select_list[0]) is None:
        return sql

    # One base table. A join or subquery makes "the set of values" ambiguous
    # enough not to guess at.
    from_table = node.get("from_table") or {}
    if from_table.get("type") != "BASE_TABLE":
        return sql

    node["modifiers"] = [dict(_DISTINCT_MODIFIER)]

    try:
        repaired = conn.execute(
            "SELECT json_deserialize_sql(?::JSON)", [json.dumps(payload)]
        ).fetchone()[0]
    except Exception as exc:  # noqa: BLE001
        logger.debug("distinct repair: could not rebuild SQL (%s)", exc)
        return sql

    if not repaired or not isinstance(repaired, str):
        return sql

    from app.nl2sql.sql_validator import is_safe_sql

    if not is_safe_sql(repaired):
        return sql

    logger.info("Added DISTINCT to a value-listing query")
    return repaired


# ── Date period bounds (issue #69) ────────────────────────────────────────────
#
# `date` was the largest category gap (7/15) once #58 took `distinct` to 100%,
# and all three failures were one defect: a period in the question becomes a
# single boundary instead of a range.
#
#   "total revenue in March 2024"  -> WHERE order_date = '2024-03-01'
#   "employees joined in 2021"     -> WHERE join_date  = '2021-01-01'
#   "employees joined before 2020" -> WHERE join_date  < '2020-12-31'
#
# Each returns a plausible, silently wrong number — one day's revenue reads as a
# bad month, not as a bug.
#
# This is a better candidate for a deterministic repair than #58 was, and much
# better than #59: the correct half-open range is *derived* from the period the
# question names, not guessed. "March 2024" can only mean
# [2024-03-01, 2024-04-01). There is nothing to infer.
#
# Two dead ends, both checked, recorded so they aren't re-derived: this is not
# #61 (dates typed TIMESTAMP_NS — genuinely fixed; this SQL binds and runs), and
# not eval strictness over an extra projected column (`subset_ok` permutes
# columns; the comparison fails on row count).

_MONTHS = {
    m: i + 1 for i, m in enumerate(
        "january february march april may june july august september october "
        "november december".split()
    )
}

# "in March 2024" / "during March 2024". Requires the month name, so
# "in the first quarter of 2024" cannot match — those cases already pass and
# must not be disturbed.
_MONTH_PERIOD = re.compile(
    r"\b(?P<prep>in|during|before|after)\s+(?P<month>"
    + "|".join(_MONTHS)
    + r")\s+(?P<year>\d{4})\b"
)
# "in 2021" / "before 2020". The year must follow the preposition directly,
# which is what keeps "…of 2024" phrasings out.
_YEAR_PERIOD = re.compile(r"\b(?P<prep>in|during|before|after)\s+(?P<year>\d{4})\b")

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_EQUAL = "COMPARE_EQUAL"
_LESS = {"COMPARE_LESSTHAN", "COMPARE_LESSTHANOREQUALTO"}
_GREATER = {"COMPARE_GREATERTHAN", "COMPARE_GREATERTHANOREQUALTO"}


def _month_start(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}-01"


def _next_month(year: int, month: int) -> tuple[int, int]:
    return (year + 1, 1) if month == 12 else (year, month + 1)


def _period_from_question(question: str) -> tuple[str, str, str] | None:
    """Return ``(kind, start, end)`` for the period the question names.

    ``kind`` is ``within``/``before``/``after``; ``start`` and ``end`` are ISO
    dates bounding it as a half-open interval ``[start, end)``.

    Returns None when no period is named, or when more than one distinct period
    is — "orders in 2020 compared to 2021" is not something to guess at.
    """
    q = question.casefold()

    found: set[tuple[str, str, str]] = set()
    for m in _MONTH_PERIOD.finditer(q):
        year, month = int(m.group("year")), _MONTHS[m.group("month")]
        ny, nm = _next_month(year, month)
        found.add((m.group("prep"), _month_start(year, month), _month_start(ny, nm)))
    if not found:
        for m in _YEAR_PERIOD.finditer(q):
            year = int(m.group("year"))
            found.add((m.group("prep"), f"{year:04d}-01-01", f"{year + 1:04d}-01-01"))

    if len(found) != 1:
        return None
    prep, start, end = found.pop()
    kind = {"in": "within", "during": "within"}.get(prep, prep)
    return kind, start, end


def _date_literal(node: dict) -> str | None:
    """The ISO date a CONSTANT node holds, or None if it isn't one."""
    if node.get("class") != "CONSTANT":
        return None
    value = (node.get("value") or {}).get("value")
    if not isinstance(value, str) or not _ISO_DATE.match(value):
        return None
    return value


def _comparison_candidates(node, out: list) -> None:
    """Collect COMPARISON nodes of the form ``<column> <op> '<iso date>'``."""
    if isinstance(node, list):
        for item in node:
            _comparison_candidates(item, out)
        return
    if not isinstance(node, dict):
        return
    if (
        node.get("class") == "COMPARISON"
        and (node.get("left") or {}).get("class") == _COLUMN_REF
        and _date_literal(node.get("right") or {}) is not None
    ):
        out.append(node)
        return  # don't descend into a node already claimed
    for value in node.values():
        _comparison_candidates(value, out)


def _with_literal(comparison: dict, op: str, literal: str) -> dict:
    node = json.loads(json.dumps(comparison))  # deep copy
    node["type"] = op
    node["right"]["value"]["value"] = literal
    return node


def repair_date_period_bounds(sql: str, question: str, conn) -> str:
    """Widen a single-date comparison into the period the question named (#69).

    Fires only when the question names exactly one period, the WHERE clause
    holds exactly one ``column <op> 'YYYY-MM-DD'`` comparison, and that
    comparison's literal falls inside the period — so a query that already
    expresses the range correctly has no bare comparison to rewrite and is left
    alone. Returns the input unchanged for anything else. Never raises.
    """
    period = _period_from_question(question)
    if period is None:
        return sql
    kind, start, end = period

    try:
        serialized = conn.execute("SELECT json_serialize_sql(?::VARCHAR)", [sql]).fetchone()[0]
        payload = json.loads(serialized)
    except Exception as exc:  # noqa: BLE001 — repair is best-effort by design
        logger.debug("date-period repair: could not parse SQL (%s)", exc)
        return sql

    if payload.get("error"):
        return sql
    statements = payload.get("statements") or []
    if len(statements) != 1:
        return sql
    node = statements[0].get("node") or {}
    if node.get("type") != _SELECT_NODE:
        return sql

    where = node.get("where_clause")
    if not where:
        return sql

    candidates: list[dict] = []
    _comparison_candidates(where, candidates)
    # More than one date comparison means the model already built *some* range,
    # or the filter is compound in a way this cannot reason about safely.
    if len(candidates) != 1:
        return sql

    comparison = candidates[0]
    literal = _date_literal(comparison["right"])
    op = comparison.get("type")

    # The literal must sit inside the named period. Otherwise the question and
    # the SQL are talking about different things, and this is not the defect.
    if not (start <= literal < end):
        return sql

    if kind == "within" and op == _EQUAL:
        # d = '2024-03-01'  ->  d >= '2024-03-01' AND d < '2024-04-01'
        replacement = {
            "class": "CONJUNCTION",
            "type": "CONJUNCTION_AND",
            "alias": "",
            "query_location": comparison.get("query_location", 0),
            "children": [
                _with_literal(comparison, "COMPARE_GREATERTHANOREQUALTO", start),
                _with_literal(comparison, "COMPARE_LESSTHAN", end),
            ],
        }
    elif kind == "before" and op in _LESS:
        # "before 2020" with d < '2020-12-31' admits the whole of 2020.
        if literal == start:
            return sql  # already correct
        replacement = _with_literal(comparison, "COMPARE_LESSTHAN", start)
    elif kind == "after" and op in _GREATER:
        # "after 2021" means from 2022-01-01, not from some day inside 2021.
        if literal == end:
            return sql
        replacement = _with_literal(comparison, "COMPARE_GREATERTHANOREQUALTO", end)
    else:
        return sql

    comparison.clear()
    comparison.update(replacement)

    try:
        repaired = conn.execute(
            "SELECT json_deserialize_sql(?::JSON)", [json.dumps(payload)]
        ).fetchone()[0]
    except Exception as exc:  # noqa: BLE001
        logger.debug("date-period repair: could not rebuild SQL (%s)", exc)
        return sql

    if not repaired or not isinstance(repaired, str):
        return sql

    from app.nl2sql.sql_validator import is_safe_sql

    if not is_safe_sql(repaired):
        return sql

    logger.info("Widened a date comparison to the period the question named")
    return repaired


# ── Missing GROUP BY entirely (issue #73) ─────────────────────────────────────
#
# The natural extension of #52. That repair fixes "GROUP BY present, key missing
# from the projection". This one fixes the case where the model dropped the
# grouping altogether and answered a per-group question with one scalar:
#
#   "What is the total revenue for each category?"
#     -> SELECT SUM(total_amount) AS total_revenue FROM sales_data
#   "What is the total Electronics revenue in each region?"
#     -> SELECT SUM(CASE WHEN category = 'Electronics' THEN total_amount ELSE 0 END)
#        FROM sales_data
#
# Both return a single number where the user asked for one per group, and both
# fail every run. `add_missing_group_keys` cannot help: it keys off an existing
# GROUP BY, and there isn't one.
#
# Like #69 and unlike #59, the answer is *derived*: the question names the
# dimension ("each category"), and the name is then checked against the table's
# real columns before anything is rewritten. Nothing is guessed.
#
# Note it does not unwrap the CASE WHEN pivot in the second example — grouping
# it is already the fix for the reported defect, and unwrapping is a separate,
# riskier transform. The pivot keeps groups with a zero total that the reference
# query omits, so on data where some group has no matching rows the two still
# differ. Worth knowing before relying on this for more than the grouping.

# Aggregates whose presence means "this projection collapses rows".
_AGGREGATE_NAMES = frozenset(
    "sum count avg mean min max median mode total "
    "stddev stddev_samp stddev_pop var_samp var_pop variance "
    "count_star string_agg list arg_min arg_max quantile quantile_cont".split()
)

# "for each category", "in each region", "per region", "by category".
_DIMENSION_CUES = (
    re.compile(r"\beach\s+(?P<dim>[a-z_]+)\b"),
    re.compile(r"\bper\s+(?P<dim>[a-z_]+)\b"),
    # "by X" is the canonical phrasing but also appears in "sorted by", "ranked
    # by", "ordered by" — none of which ask for a grouping.
    re.compile(r"(?<!sorted )(?<!ordered )(?<!ranked )(?<!grouped )\bby\s+(?P<dim>[a-z_]+)\b"),
)


def _is_aggregate(node: dict) -> bool:
    return (
        node.get("class") == "FUNCTION"
        and str(node.get("function_name", "")).casefold() in _AGGREGATE_NAMES
    )


def _table_columns(conn, table: str) -> list[str]:
    try:
        rows = conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = ?",
            [table],
        ).fetchall()
    except Exception:  # noqa: BLE001 — repair is best-effort by design
        return []
    return [r[0] for r in rows]


def _match_dimension(question: str, columns: list[str]) -> str | None:
    """The column the question names as its grouping dimension, if exactly one.

    Matching is against real columns, so a stray "each" in a question that names
    nothing groupable cannot produce a rewrite.
    """
    by_folded = {c.casefold(): c for c in columns}
    q = question.casefold()

    found: set[str] = set()
    for cue in _DIMENSION_CUES:
        for m in cue.finditer(q):
            for candidate in _singular_plural_forms(m.group("dim")):
                if candidate in by_folded:
                    found.add(by_folded[candidate])
                    break
    return found.pop() if len(found) == 1 else None


def _singular_plural_forms(word: str) -> tuple[str, ...]:
    """The word plus the obvious singular/plural variants of it.

    Enough to bridge "each categories" to a ``category`` column and "by regions"
    to ``region``. Not a stemmer: anything it fails to resolve simply means no
    column matches and no rewrite happens, which is the safe direction.

    Note ``str.rstrip('s')`` is wrong here — it strips *every* trailing s, so
    "class" becomes "clas".
    """
    forms = [word]
    if word.endswith("ies"):
        forms.append(word[:-3] + "y")          # categories -> category
    elif word.endswith("es"):
        forms.append(word[:-2])                # boxes -> box
        forms.append(word[:-1])                # prices -> price
    elif word.endswith("s") and not word.endswith("ss"):
        forms.append(word[:-1])                # regions -> region
    elif not word.endswith("s"):
        forms.append(word + "s")               # region -> regions
    # A word ending in "ss" ("class") is not a plural and yields no variant.
    return tuple(forms)


def add_missing_group_by(sql: str, question: str, conn) -> str:
    """Add the GROUP BY a per-group question asked for but the SQL omitted.

    Fires only when the question names exactly one real column as its dimension,
    the query aggregates, has no GROUP BY at all, no modifiers, and reads from a
    single base table. Returns the input unchanged otherwise. Never raises.
    """
    try:
        serialized = conn.execute("SELECT json_serialize_sql(?::VARCHAR)", [sql]).fetchone()[0]
        payload = json.loads(serialized)
    except Exception as exc:  # noqa: BLE001
        logger.debug("group-by repair: could not parse SQL (%s)", exc)
        return sql

    if payload.get("error"):
        return sql
    statements = payload.get("statements") or []
    if len(statements) != 1:
        return sql
    node = statements[0].get("node") or {}

    if node.get("type") != _SELECT_NODE:
        return sql
    # Only the "no grouping at all" case — #52's repair owns the rest.
    if node.get("group_expressions"):
        return sql
    if node.get("aggregate_handling") not in (None, "STANDARD_HANDLING"):
        return sql
    # ORDER BY/LIMIT over an ungrouped aggregate means something quite different
    # once grouped; don't reinterpret it.
    if node.get("modifiers") or node.get("qualify") or node.get("sample"):
        return sql

    from_table = node.get("from_table") or {}
    if from_table.get("type") != "BASE_TABLE":
        return sql
    table = from_table.get("table_name")
    if not table:
        return sql

    select_list = node.get("select_list") or []
    if not select_list or _projects_everything(select_list):
        return sql
    # Must actually aggregate. Without this, "the region for each order" — a
    # plain projection — would get a spurious GROUP BY.
    if not any(_is_aggregate(item) for item in select_list):
        return sql

    dimension = _match_dimension(question, _table_columns(conn, table))
    if dimension is None:
        return sql

    # No "is it already projected?" check here, deliberately. For that to
    # matter the query would need an aggregate, no GROUP BY, and the dimension
    # projected as a bare column — which DuckDB rejects outright ("column must
    # appear in the GROUP BY clause"), so it cannot reach this function. A
    # mutation removing such a guard killed no test, which is how it was found.

    column_ref = {
        "class": _COLUMN_REF,
        "type": _COLUMN_REF,
        "alias": "",
        "query_location": 0,
        "column_names": [dimension],
    }
    node["group_expressions"] = [json.loads(json.dumps(column_ref))]
    node["group_sets"] = [[0]]
    # Dimension first, then the measures — the order a reader expects and the
    # one the reference answers use, matching #52's repair.
    node["select_list"] = [json.loads(json.dumps(column_ref))] + select_list

    try:
        repaired = conn.execute(
            "SELECT json_deserialize_sql(?::JSON)", [json.dumps(payload)]
        ).fetchone()[0]
    except Exception as exc:  # noqa: BLE001
        logger.debug("group-by repair: could not rebuild SQL (%s)", exc)
        return sql

    if not repaired or not isinstance(repaired, str):
        return sql

    # It has to actually run — a grouped rewrite can fail to bind in ways the
    # ungrouped original did not.
    try:
        conn.execute(f"EXPLAIN {repaired}")
    except Exception as exc:  # noqa: BLE001
        logger.debug("group-by repair: rewrite does not bind (%s)", exc)
        return sql

    from app.nl2sql.sql_validator import is_safe_sql

    if not is_safe_sql(repaired):
        return sql

    logger.info("Added the GROUP BY a per-group question asked for")
    return repaired
