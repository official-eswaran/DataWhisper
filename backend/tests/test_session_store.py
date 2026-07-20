import fakeredis
import pytest

from app.core.session_store import InMemoryConversationStore, RedisConversationStore


@pytest.fixture(params=["memory", "redis"])
def store(request):
    if request.param == "memory":
        return InMemoryConversationStore(ttl_seconds=60, max_entries=100)
    return RedisConversationStore(fakeredis.FakeStrictRedis(), ttl_seconds=60)


def test_history_roundtrip(store):
    sid = "s1"
    assert store.get_history(sid) == []
    store.append_turn(sid, "q1", "sql1")
    store.append_turn(sid, "q2", "sql2")
    hist = store.get_history(sid)
    assert [m["content"] for m in hist] == ["q1", "sql1", "q2", "sql2"]


def test_history_is_trimmed_to_last_three_pairs(store):
    sid = "s2"
    for i in range(5):
        store.append_turn(sid, f"q{i}", f"sql{i}")
    hist = store.get_history(sid)
    assert len(hist) == 6  # last 3 Q&A pairs
    assert hist[0]["content"] == "q2"


def test_schema_cache(store):
    sid = "s3"
    assert store.get_schema(sid) is None
    store.set_schema(sid, "Table: sales(...)")
    assert store.get_schema(sid) == "Table: sales(...)"


def test_invalidate_clears(store):
    sid = "s4"
    store.append_turn(sid, "q", "a")
    store.set_schema(sid, "schema")
    store.invalidate(sid)
    assert store.get_history(sid) == []
    assert store.get_schema(sid) is None


def test_inmemory_lru_eviction():
    s = InMemoryConversationStore(ttl_seconds=60, max_entries=2)
    s.append_turn("a", "q", "x")
    s.append_turn("b", "q", "x")
    s.append_turn("c", "q", "x")  # evicts "a" (least recently used)
    assert s.get_history("a") == []
    assert s.get_history("c") != []
