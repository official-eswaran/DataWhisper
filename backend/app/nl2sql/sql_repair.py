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
