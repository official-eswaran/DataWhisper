"""Prompt assembly contract.

Deliberately thin, and the reason is worth recording. This file was first
written to pin three new rules aimed at #58/#59/#60. All three were measured on
the eval and reverted — see PROJECT_STATUS.md — so the tests that asserted their
presence went with them. What survives is the mechanical contract: the question,
schema and history actually reach the model, and the rule numbering stays
consistent.

A test that asserts a rule *exists* proves nothing about accuracy anyway. Only
``backend/evals/`` can measure whether the model obeys it, which is the whole
lesson of #52.
"""
from app.nl2sql.prompt_builder import build_nl2sql_prompt

SCHEMA = "sales_data(order_id INTEGER, product VARCHAR, quantity INTEGER, price DOUBLE)"


def test_schema_and_question_are_interpolated():
    p = build_nl2sql_prompt("Which region sold most?", SCHEMA)
    assert SCHEMA in p
    assert "Which region sold most?" in p


def test_history_is_included_when_present():
    p = build_nl2sql_prompt(
        "and by month?",
        SCHEMA,
        history=[
            {"role": "user", "content": "revenue by region"},
            {"role": "assistant", "content": "SELECT region, SUM(x) FROM t GROUP BY region"},
        ],
    )
    assert "Previous Conversation" in p
    assert "revenue by region" in p
    assert "SELECT region, SUM(x) FROM t GROUP BY region" in p


def test_history_is_omitted_when_absent():
    assert "Previous Conversation" not in build_nl2sql_prompt("q", SCHEMA)


def test_history_is_capped_to_the_recent_turns():
    """Unbounded history would grow the prompt without limit on a long session."""
    history = [{"role": "user", "content": f"question number {i}"} for i in range(20)]
    p = build_nl2sql_prompt("latest", SCHEMA, history=history)
    assert "question number 19" in p
    assert "question number 0" not in p


def test_the_group_by_key_rule_is_still_stated():
    """sql_repair.py's docstring cites "prompt_builder rule 8" as the rule the
    model ignores. If the rule is ever renumbered or removed, that reference
    goes stale and the repair's rationale stops making sense."""
    p = build_nl2sql_prompt("q", SCHEMA)
    assert "8. CRITICAL: ALWAYS include every GROUP BY column in the SELECT clause." in p


def test_rule_numbering_has_no_duplicates():
    p = build_nl2sql_prompt("q", SCHEMA)
    rules = [
        ln.split(".", 1)[0].strip()
        for ln in p.splitlines()
        if ln[:1].isdigit() and "." in ln
    ]
    assert len(rules) == len(set(rules)), f"duplicate rule numbers: {rules}"
