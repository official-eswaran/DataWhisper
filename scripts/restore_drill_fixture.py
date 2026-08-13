#!/usr/bin/env python3
"""Seed, destroy-check and verify state for the restore drill (issue #18).

Run from ``backend/`` with the app importable::

    cd backend && PYTHONPATH=. python3 ../scripts/restore_drill_fixture.py seed

``restore_drill.sh`` is the entry point; this file is the half that has to know
what DataWhisper's data *means*. It deliberately goes through the application's
own writers (`write_audit_log`, `verify_audit_chain`) rather than raw SQL, so
the drill proves the restored database is usable by the app — not merely that
rows came back.

Three subcommands, and the middle one is the point:

  seed             write known state into the database and DATA_DIR
  assert-destroyed fail unless that state is genuinely gone
  verify           fail unless it came back intact

Without ``assert-destroyed`` the drill proves nothing: a restore that silently
did nothing would pass a seed→verify pair, because the state never left. It is
the same instinct as mutation-testing a test.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ORG_SLUG = "restore-drill"
ORG_NAME = "Restore Drill Org"
USERNAME = "drill-operator"
EMAIL = "drill@example.invalid"
# Enough entries to make a chain worth verifying, few enough to stay instant.
AUDIT_ENTRIES = 12
# Stand-in for a per-session DuckDB dataset. Content is arbitrary; what matters
# is that the bytes survive the round trip exactly.
DATASET_NAME = "restore-drill-session.duckdb"
UPLOAD_NAME = "uploads/restore-drill.csv"
DATASET_BYTES = b"DUCKDB-DRILL-FIXTURE\x00\x01\x02" * 512
UPLOAD_BYTES = b"order_id,product,quantity\n1,Laptop,2\n2,Mouse,5\n"

STATE_FILE = "restore-drill-state.json"


def _data_dir() -> Path:
    """Where the drill writes its stand-in dataset files.

    The shell scripts call this ``DATA_DIR``; the app calls its own copy
    ``DATABASE_DIR``. ``restore_drill.sh`` points both at the same scratch
    directory, which is the only configuration in which backup.sh's SQLite
    branch can find ``app.db`` at all.
    """
    from app.core.config import settings

    return Path(settings.DATABASE_DIR)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _files() -> dict[str, bytes]:
    return {DATASET_NAME: DATASET_BYTES, UPLOAD_NAME: UPLOAD_BYTES}


def _fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def _org_id(conn) -> int | None:
    from sqlalchemy import select

    from app.core.database import organizations

    return conn.execute(
        select(organizations.c.id).where(organizations.c.slug == ORG_SLUG)
    ).scalar()


# ── seed ──────────────────────────────────────────────────────────────────────

def seed(state_path: Path) -> None:
    from sqlalchemy import insert, select

    from app.core.database import (
        GENESIS_HASH,
        audit_chain_state,
        get_engine,
        organizations,
        users,
        write_audit_log,
    )
    from app.core.security import hash_password

    engine = get_engine()
    with engine.begin() as conn:
        if _org_id(conn) is not None:
            _fail(
                f"an org with slug {ORG_SLUG!r} already exists — refusing to seed "
                "over it. The drill must run against a scratch database."
            )
        org_id = conn.execute(
            insert(organizations).values(name=ORG_NAME, slug=ORG_SLUG, plan="pro")
        ).inserted_primary_key[0]
        conn.execute(insert(audit_chain_state).values(org_id=org_id, last_hash=GENESIS_HASH))
        conn.execute(
            insert(users).values(
                org_id=org_id,
                username=USERNAME,
                email=EMAIL,
                password_hash=hash_password("drill-only-not-a-real-secret"),
                role="owner",
                email_verified=True,
            )
        )

    # Through the real writer, so the hash chain is a real one. A chain built by
    # hand would verify against itself and prove nothing about the app.
    for i in range(AUDIT_ENTRIES):
        write_audit_log(
            USERNAME, org_id, f"drill-session-{i:02d}",
            f"drill question {i}", "SELECT 1", "1 row", "success",
        )

    data_dir = _data_dir()
    for name, payload in _files().items():
        path = data_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)

    with get_engine().connect() as conn:
        entries = conn.execute(_audit_count_stmt(org_id)).scalar()

    state = {
        "org_id": org_id,
        "audit_entries": int(entries or 0),
        "files": {name: _sha256(payload) for name, payload in _files().items()},
        "data_dir": str(data_dir),
    }
    state_path.write_text(json.dumps(state, indent=1))
    print(
        f"seeded org {org_id} ({ORG_SLUG}), {state['audit_entries']} audit entries, "
        f"{len(state['files'])} files under {data_dir}"
    )


def _audit_count_stmt(org_id: int):
    from sqlalchemy import func, select

    from app.core.database import audit_logs

    return select(func.count()).select_from(audit_logs).where(audit_logs.c.org_id == org_id)


# ── assert-destroyed ──────────────────────────────────────────────────────────

def assert_destroyed(state_path: Path) -> None:
    """Fail unless the seeded state is really gone.

    This is what stops the drill from certifying a no-op. A restore step that
    did nothing at all would still leave `verify` passing if the destruction
    never happened.
    """
    state = json.loads(state_path.read_text())
    problems: list[str] = []

    try:
        from app.core.database import get_engine

        with get_engine().connect() as conn:
            if _org_id(conn) is not None:
                problems.append(f"org {ORG_SLUG!r} is still in the database")
    except Exception as exc:  # noqa: BLE001
        # No database at all is a legitimate "destroyed" state — the SQLite
        # file is deleted outright, and the engine cannot connect.
        print(f"  (database unreachable, which counts as destroyed: {exc})")

    data_dir = Path(state["data_dir"])
    for name in state["files"]:
        if (data_dir / name).exists():
            problems.append(f"{name} is still on disk")

    if problems:
        _fail(
            "destruction step did not destroy anything — the restore that "
            "follows would prove nothing:\n  - " + "\n  - ".join(problems)
        )
    print("confirmed destroyed: org gone, dataset files gone")


# ── verify ────────────────────────────────────────────────────────────────────

def verify(state_path: Path) -> None:
    from sqlalchemy import select

    from app.core.database import get_engine, users, verify_audit_chain

    state = json.loads(state_path.read_text())
    problems: list[str] = []

    with get_engine().connect() as conn:
        org_id = _org_id(conn)
        if org_id is None:
            _fail(f"org {ORG_SLUG!r} did not come back — the restore failed")
        if org_id != state["org_id"]:
            problems.append(
                f"org id changed across the restore: {state['org_id']} -> {org_id}. "
                "Foreign keys elsewhere point at the old value."
            )
        row = conn.execute(
            select(users.c.username, users.c.role, users.c.email_verified)
            .where(users.c.org_id == org_id)
        ).first()
        if row is None:
            problems.append("the org came back but its owner did not")
        else:
            if row.username != USERNAME:
                problems.append(f"username is {row.username!r}, expected {USERNAME!r}")
            if row.role != "owner":
                problems.append(f"role is {row.role!r}, expected 'owner'")
            if not row.email_verified:
                problems.append(
                    "email_verified came back false — the restored owner would be "
                    "locked out of queries by the #21 gate"
                )
        entries = conn.execute(_audit_count_stmt(org_id)).scalar()
        if int(entries or 0) != state["audit_entries"]:
            problems.append(
                f"audit entries: {entries} restored, {state['audit_entries']} backed up"
            )

    # The runbook's headline post-restore check, run against the database
    # directly rather than through a live server: a chain that verifies proves the
    # entries came back in order, unmodified, with their links intact.
    chain = verify_audit_chain(state["org_id"], full=True)
    if not chain.get("valid"):
        problems.append(f"audit chain does not verify after restore: {chain}")

    data_dir = Path(state["data_dir"])
    for name, expected in state["files"].items():
        path = data_dir / name
        if not path.exists():
            problems.append(f"{name} was not restored")
        elif _sha256(path.read_bytes()) != expected:
            problems.append(f"{name} came back with different bytes")

    if problems:
        _fail("restored state does not match what was backed up:\n  - " + "\n  - ".join(problems))
    print(
        f"verified: org {state['org_id']}, owner {USERNAME}, "
        f"{state['audit_entries']} audit entries, chain valid, "
        f"{len(state['files'])} files byte-identical"
    )


COMMANDS = {"seed": seed, "assert-destroyed": assert_destroyed, "verify": verify}


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(f"usage: {sys.argv[0]} {{{'|'.join(COMMANDS)}}} [state-file]", file=sys.stderr)
        raise SystemExit(2)
    state_path = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(STATE_FILE)
    COMMANDS[sys.argv[1]](state_path)


if __name__ == "__main__":
    main()
