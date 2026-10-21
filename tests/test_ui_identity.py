"""The chat API behind the gateway: who is calling is believed only when the request carries the gateway key, and conversations and memory are then per user.
Without a gateway key the app is the single-user app it was before."""
import json
import sqlite3
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from ui.backend import main, store  # noqa: E402

KEY = "test-gateway-key"


def gateway(user: str | None = "u-1", **extra) -> dict:
    return {"x-gateway-key": KEY, **({"x-user-id": user} if user else {}), **extra}


class FakeMemories:
    def __init__(self):
        self.items: dict[str, dict[str, str]] = {}  # scope -> {memory id: content}
        self.scopes_used: list[str] = []
        self.deleted: list[str] = []

    def list_memories(self, store_name, scope, limit=100):
        self.scopes_used.append(scope)
        return [SimpleNamespace(memory_id=i, kind="user_profile", content=c, updated_at=None) for i, c in self.items.get(scope, {}).items()]

    def create_memory(self, store_name, scope, content, kind):
        self.scopes_used.append(scope)
        memory_id = f"mem_{sum(len(v) for v in self.items.values()) + 1}"
        self.items.setdefault(scope, {})[memory_id] = content
        return SimpleNamespace(memory_id=memory_id, kind=kind, content=content, updated_at=None)

    def delete_memory(self, store_name, memory_id):
        self.deleted.append(memory_id)
        for memories in self.items.values():
            memories.pop(memory_id, None)

    def delete_scope(self, store_name, scope):
        self.scopes_used.append(scope)
        self.items.pop(scope, None)


class FakeOpenAI:
    """Just enough of the Foundry project's OpenAI client for one chat turn."""

    def __init__(self, delay: float = 0.0):
        self.delay, self.conversations_made, self.stream_calls = delay, 0, []
        self.conversations = SimpleNamespace(create=self._conversation, delete=lambda conversation_id: None)
        self.responses = SimpleNamespace(create=self._response, retrieve=lambda *a, **k: iter(()))

    def _conversation(self):
        self.conversations_made += 1
        return SimpleNamespace(id=f"conv_{self.conversations_made}")

    def _response(self, **kwargs):
        if not kwargs.get("stream"):  # the call that writes the conversation's title
            return SimpleNamespace(output_text="A short title")
        self.stream_calls.append(kwargs)
        return self._events()

    def _events(self):
        yield SimpleNamespace(type="response.created", response=SimpleNamespace(id="resp_1"), sequence_number=0)
        time.sleep(self.delay)
        yield SimpleNamespace(type="response.output_text.delta", delta="Hello there", sequence_number=1)
        yield SimpleNamespace(type="response.completed", response=SimpleNamespace(usage=SimpleNamespace(input_tokens=3, output_tokens=2)), sequence_number=2)


@pytest.fixture
def api(monkeypatch, tmp_path):
    monkeypatch.delenv("GATEWAY_KEY", raising=False)
    monkeypatch.delenv("SHARE_HUB_EVENTS", raising=False)
    monkeypatch.delenv("ENABLE_STREAM_CHECK", raising=False)
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "chat.db")
    monkeypatch.setattr(store, "_db", None)
    memories, openai = FakeMemories(), FakeOpenAI()
    hub_polls: list[int] = []

    async def hub_events(http, since):
        hub_polls.append(since)
        return None

    monkeypatch.setattr(main, "_project", lambda: SimpleNamespace(beta=SimpleNamespace(memory_stores=memories)))
    monkeypatch.setattr(main, "_openai", lambda: openai)
    monkeypatch.setattr(main, "_hub_events", hub_events)
    monkeypatch.setattr(main, "_env", lambda: {"HUB_URL": "http://hub.invalid", "HUB_API_KEY": "unused", "FOUNDRY_PROJECT_ENDPOINT": "https://x.invalid/api/projects/p"})
    yield SimpleNamespace(client=TestClient(main.app), openai=openai, memories=memories, hub_polls=hub_polls, monkeypatch=monkeypatch)
    if store._db is not None:
        store._db.close()


def behind_gateway(api):
    api.monkeypatch.setenv("GATEWAY_KEY", KEY)


def sse(text: str) -> list[tuple[str, object]]:
    """The frames of a server-sent-events body: ("comment", line) for a comment, (event name, parsed data) for an event."""
    frames = []
    for frame in (f for f in text.split("\n\n") if f.strip()):
        if frame.startswith(":"):
            frames.append(("comment", frame))
            continue
        lines = frame.splitlines()
        frames.append((next(l[7:] for l in lines if l.startswith("event: ")), json.loads(next(l[6:] for l in lines if l.startswith("data: ")))))
    return frames


# ---------------------------------------------------------------- trust

def test_without_a_gateway_key_the_identity_headers_are_ignored(api):
    r = api.client.get("/api/me", headers={"x-user-id": "attacker", "x-gateway-key": "anything", "x-user-name": "Mallory"})
    assert r.status_code == 200
    assert r.json() == {"user_id": None, "name": None, "authenticated": False, "gateway": False}
    store.ensure_conversation("conv_default", "The default user's chat")
    store.ensure_conversation("conv_u1", "Another user's chat", user_id="u-1")
    listed = api.client.get("/api/conversations", headers={"x-user-id": "u-1"}).json()
    assert [c["id"] for c in listed] == ["conv_default"]


@pytest.mark.parametrize("method, path", [("GET", "/api/me"), ("GET", "/api/health"), ("GET", "/api/conversations"), ("GET", "/api/memory"),
                                          ("DELETE", "/api/memory"), ("POST", "/api/chat"), ("GET", "/api/case/C-1")])
def test_behind_the_gateway_a_request_without_the_key_is_refused(api, method, path):
    behind_gateway(api)
    body = {"json": {"message": "hi"}} if method == "POST" else {}
    for headers in ({}, {"x-user-id": "forged"}, {"x-user-id": "forged", "x-gateway-key": "wrong"}, {"x-gateway-key": KEY[:-1]}):
        assert api.client.request(method, path, headers=headers, **body).status_code == 401
    assert api.openai.conversations_made == 0 and api.memories.scopes_used == []


def test_behind_the_gateway_the_user_id_is_required_and_checked(api):
    behind_gateway(api)
    assert api.client.get("/api/me", headers=gateway(user=None)).status_code == 401
    for bad in ("a b", "../x", "x" * 200, "-leading-dash", "semi;colon"):
        assert api.client.get("/api/me", headers=gateway(user=bad)).status_code == 400, bad


def test_the_gateway_key_and_user_headers_identify_the_caller(api):
    behind_gateway(api)
    r = api.client.get("/api/me", headers=gateway("0b1c2d3e-0000-4000-8000-123456789abc", **{"x-user-name": "Asha Rao"}))
    assert r.json() == {"user_id": "0b1c2d3e-0000-4000-8000-123456789abc", "name": "Asha Rao", "authenticated": True, "gateway": True}
    assert api.client.get("/api/me", headers=gateway("sub-front-channel")).json()["user_id"] == "sub-front-channel"


def test_the_interactive_docs_are_not_served_behind_the_gateway(monkeypatch):
    import importlib

    monkeypatch.setenv("GATEWAY_KEY", KEY)
    reloaded = importlib.reload(main)
    try:
        assert reloaded.app.openapi_url is None and reloaded.app.docs_url is None
    finally:
        monkeypatch.delenv("GATEWAY_KEY")
        importlib.reload(main)


# ---------------------------------------------------------------- conversations

def test_each_user_sees_only_their_own_conversations(api):
    behind_gateway(api)
    store.ensure_conversation("conv_a", "A's chat", user_id="u-a")
    store.ensure_conversation("conv_b", "B's chat", user_id="u-b")
    store.ensure_conversation("conv_old", "Written before users existed")  # the default user's
    assert [c["id"] for c in api.client.get("/api/conversations", headers=gateway("u-a")).json()] == ["conv_a"]
    assert [c["id"] for c in api.client.get("/api/conversations", headers=gateway("u-b")).json()] == ["conv_b"]
    assert api.client.get("/api/conversations/conv_a", headers=gateway("u-a")).json()["title"] == "A's chat"


def test_one_user_cannot_read_rename_or_delete_anothers_conversation(api):
    behind_gateway(api)
    store.ensure_conversation("conv_a", "A's chat", user_id="u-a")
    b = gateway("u-b")
    assert api.client.get("/api/conversations/conv_a", headers=b).status_code == 404
    assert api.client.patch("/api/conversations/conv_a", json={"title": "mine now"}, headers=b).status_code == 404
    assert api.client.delete("/api/conversations/conv_a", headers=b).status_code == 404
    assert api.client.get("/api/conversations/conv_a", headers=gateway("u-a")).json()["title"] == "A's chat"
    assert api.client.delete("/api/conversations/conv_a", headers=gateway("u-a")).status_code == 200
    assert store.owner_of("conv_a") is None


def test_a_history_from_before_users_existed_belongs_to_the_default_user(monkeypatch, tmp_path):
    old = tmp_path / "v1.db"
    db = sqlite3.connect(old)
    db.executescript("CREATE TABLE conversations (id TEXT PRIMARY KEY, title TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL);"
                     "CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id TEXT NOT NULL, turn INTEGER NOT NULL, role TEXT NOT NULL,"
                     " text TEXT NOT NULL, status TEXT NOT NULL, seconds REAL, created_at REAL NOT NULL);"
                     "INSERT INTO conversations VALUES ('conv_v1', 'From v1', 1, 2);")
    db.commit()
    db.close()
    monkeypatch.setattr(store, "DB_PATH", old)
    monkeypatch.setattr(store, "_db", None)
    try:
        assert [c["id"] for c in store.list_conversations()] == ["conv_v1"]
        assert store.list_conversations("u-1") == []
        assert store.owner_of("conv_v1") == ""
    finally:
        store._db.close()


# ---------------------------------------------------------------- memory

def test_memory_is_kept_in_one_scope_per_user(api):
    behind_gateway(api)
    api.client.post("/api/memory", json={"content": "I prefer lakh"}, headers=gateway("u-a"))
    assert api.memories.scopes_used == [f"{main.MEMORY_SCOPE}-u-a"]
    assert [m["content"] for m in api.client.get("/api/memory", headers=gateway("u-a")).json()] == ["I prefer lakh"]
    assert api.client.get("/api/memory", headers=gateway("u-b")).json() == []
    assert api.client.get("/api/health", headers=gateway("u-b")).json()["memory_scope"] == f"{main.MEMORY_SCOPE}-u-b"
    api.client.delete("/api/memory", headers=gateway("u-b"))
    assert api.memories.items[f"{main.MEMORY_SCOPE}-u-a"]  # clearing B's preferences did not touch A's


def test_one_user_cannot_delete_anothers_memory_by_id(api):
    behind_gateway(api)
    memory_id = api.client.post("/api/memory", json={"content": "A's preference"}, headers=gateway("u-a")).json()["id"]
    assert api.client.delete(f"/api/memory/{memory_id}", headers=gateway("u-b")).status_code == 404
    assert api.memories.deleted == []
    assert api.client.delete(f"/api/memory/{memory_id}", headers=gateway("u-a")).status_code == 200
    assert api.memories.deleted == [memory_id]


def test_single_user_memory_keeps_the_one_default_scope(api):
    api.client.post("/api/memory", json={"content": "I prefer crore"})
    api.client.get("/api/memory", headers={"x-user-id": "u-1"})  # ignored without a gateway key
    api.client.delete("/api/memory")
    assert set(api.memories.scopes_used) == {main.MEMORY_SCOPE}
    assert api.client.get("/api/health").json()["memory_scope"] == main.MEMORY_SCOPE


# ---------------------------------------------------------------- a chat turn

def test_a_chat_turn_belongs_to_the_caller_and_carries_their_memory_scope(api):
    behind_gateway(api)
    r = api.client.post("/api/chat", json={"message": "Hello"}, headers=gateway("u-a"))
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    frames = sse(r.text)
    kinds = [k for k, _ in frames]
    assert kinds[0] == "hub" and "turn" in kinds and "delta" in kinds and kinds[-1] == "end"
    conversation_id = dict(frames)["turn"]["conversation_id"]
    assert store.owner_of(conversation_id) == "u-a"
    assert api.openai.stream_calls[0]["extra_headers"] == {"x-memory-user-id": f"{main.MEMORY_SCOPE}-u-a"}
    assert [c["id"] for c in api.client.get("/api/conversations", headers=gateway("u-a")).json()] == [conversation_id]
    assert api.client.get("/api/conversations", headers=gateway("u-b")).json() == []


def test_a_chat_turn_on_somebody_elses_conversation_is_refused(api):
    behind_gateway(api)
    store.ensure_conversation("conv_a", "A's chat", user_id="u-a")
    r = api.client.post("/api/chat", json={"message": "Let me in", "conversation_id": "conv_a"}, headers=gateway("u-b"))
    assert r.status_code == 404
    assert api.openai.stream_calls == []
    assert api.client.post("/api/chat", json={"message": "My own chat", "conversation_id": "conv_a"}, headers=gateway("u-a")).status_code == 200
    assert len(api.openai.stream_calls) == 1


def test_a_single_user_chat_turn_is_exactly_what_it_was(api):
    r = api.client.post("/api/chat", json={"message": "Hello"}, headers={"x-user-id": "u-1", "x-gateway-key": KEY})
    frames = sse(r.text)
    assert [k for k, _ in frames if k == "comment"] == []  # no keep-alive lines unless SSE_HEARTBEAT_S is set
    call = api.openai.stream_calls[0]
    assert "extra_headers" not in call and call["extra_body"]["agent_reference"]["name"] == main.CX_AGENT
    assert store.owner_of(dict(frames)["turn"]["conversation_id"]) == ""
    assert api.hub_polls, "the single-user app still polls the hub's event log"


def test_the_hubs_event_log_is_not_shared_between_users_unless_asked(api):
    behind_gateway(api)
    api.client.post("/api/chat", json={"message": "Hello"}, headers=gateway("u-a"))
    assert api.hub_polls == []
    api.monkeypatch.setenv("SHARE_HUB_EVENTS", "1")
    api.client.post("/api/chat", json={"message": "Hello again"}, headers=gateway("u-a"))
    assert api.hub_polls


def test_a_slow_turn_sends_keep_alive_comments_when_asked(api):
    behind_gateway(api)
    api.monkeypatch.setattr(main, "HEARTBEAT_SECONDS", 0.2)
    api.openai.delay = 1.0
    frames = sse(api.client.post("/api/chat", json={"message": "Hello"}, headers=gateway("u-a")).text)
    kinds = [k for k, _ in frames]
    assert kinds.count("comment") >= 2 and kinds[-1] == "end" and "final" in kinds


# ---------------------------------------------------------------- correlation id and the streaming self-check

def test_a_well_formed_correlation_id_is_echoed(api):
    assert api.client.get("/api/me", headers={"x-correlation-id": "abc-123_XYZ.789"}).headers["x-correlation-id"] == "abc-123_XYZ.789"
    assert "x-correlation-id" not in api.client.get("/api/me", headers={"x-correlation-id": "bad id"}).headers
    assert "x-correlation-id" not in api.client.get("/api/me").headers


def test_the_stream_check_is_off_by_default_and_streams_when_enabled(api):
    assert api.client.get("/api/stream-check").status_code == 404
    api.monkeypatch.setenv("ENABLE_STREAM_CHECK", "1")
    frames = sse(api.client.get("/api/stream-check?count=3&interval=0.1").text)
    assert [k for k, _ in frames] == ["tick", "tick", "tick", "end"]
    assert [d["i"] for k, d in frames if k == "tick"] == [0, 1, 2]
    behind_gateway(api)
    assert api.client.get("/api/stream-check").status_code == 401


def test_the_stream_check_can_send_keep_alives_through_a_long_silence(api):
    api.monkeypatch.setenv("ENABLE_STREAM_CHECK", "1")
    kinds = [k for k, _ in sse(api.client.get("/api/stream-check?count=2&interval=0.5&heartbeat=0.1").text)]
    assert kinds.count("comment") >= 3 and kinds[0] == "tick" and kinds[-2:] == ["tick", "end"]
    assert "comment" not in [k for k, _ in sse(api.client.get("/api/stream-check?count=2&interval=0.3&heartbeat=0").text)]
    api.monkeypatch.setenv("SSE_HEARTBEAT_S", "0.1")  # without the parameter the chat stream's setting applies
    assert "comment" in [k for k, _ in sse(api.client.get("/api/stream-check?count=2&interval=0.5").text)]
