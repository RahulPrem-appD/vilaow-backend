"""The website assistant, against a scripted stand-in for the Anthropic API.

No test here talks to Anthropic. `get_assistant` is overridden with an
Assistant built on FakeClient, which replays the turns a test scripts and
records every request it was sent — so the tests can check both what the
visitor sees and exactly what would have been sent and paid for.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from app.config import Settings, get_settings
from app.models import Professional, Stage
from app.services.assistant import (
    FALLBACK_BETA, GLM_BASE_URL, GLM_MODEL, knowledge, professional_profile,
    search_professionals,
)

CODE = "preview-code"


# ── a scripted Anthropic ────────────────────────────────────────────────────
def text(value):
    return SimpleNamespace(type="text", text=value)


def tool_starts(name):
    return SimpleNamespace(type="content_block_start",
                           content_block=SimpleNamespace(type="tool_use", name=name))


def message(stop_reason, *content):
    return SimpleNamespace(
        stop_reason=stop_reason, content=list(content), model="claude-opus-5-5",
        usage=SimpleNamespace(input_tokens=10, output_tokens=5,
                              cache_read_input_tokens=0, cache_creation_input_tokens=0),
    )


def tool_use(name, tool_input, id="toolu_1"):
    return SimpleNamespace(type="tool_use", id=id, name=name, input=tool_input)


def answer(*chunks):
    """A turn that streams `chunks` and ends."""
    return [text(c) for c in chunks], message(
        "end_turn", SimpleNamespace(type="text", text="".join(chunks)))


class FakeStream:
    def __init__(self, events, final):
        self._events, self._final = events, final

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        return iter(self._events)

    def get_final_message(self):
        return self._final


class FakeClient:
    def __init__(self, *turns):
        self.turns = list(turns)
        self.requests = []
        # client.beta.messages.stream(...)
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self._stream))

    def _stream(self, **request):
        # The service keeps appending to its message list; keep the state as
        # it was when this request went out.
        self.requests.append({**request, "messages": list(request["messages"])})
        events, final = self.turns.pop(0)
        return FakeStream(events, final)


# ── wiring ──────────────────────────────────────────────────────────────────
@pytest.fixture
def configure():
    """Point the app at a given configuration and a given fake.

    Only the client is replaced, so `get_assistant` still decides the model
    and which of Claude's options go into a request, as it does for real.
    `_env_file=None` keeps a developer's backend/.env — which may name a real
    key or another provider — out of what the tests see.
    """
    from app.api.deps import get_assistant_client
    from app.main import app

    def apply(fake=None, **settings):
        values = {"anthropic_api_key": "test-key", "assistant_access_code": CODE} | settings
        app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None, **values)
        app.dependency_overrides[get_assistant_client] = (
            lambda: fake or FakeClient(answer("Hello.")))

    yield apply
    app.dependency_overrides.pop(get_settings, None)
    app.dependency_overrides.pop(get_assistant_client, None)


def ask(client, *messages, code=CODE):
    history = [{"role": "user" if i % 2 == 0 else "assistant", "content": m}
               for i, m in enumerate(messages)]
    headers = {"X-Assistant-Code": code} if code is not None else {}
    return client.post("/api/public/assistant/chat", json={"messages": history}, headers=headers)


def events(response):
    return [json.loads(line.removeprefix("data: "))
            for line in response.text.splitlines() if line.startswith("data: ")]


def _published(db, professions, **kw):
    base = dict(
        business_name="Crete Law", contact_name="Maria Kyriakou", email="m@example.com",
        city="Chania", region="Crete", languages=["English — Fluent", "Greek"],
        profession_id=professions["lawyer"], slug="maria-kyriakou", published=True,
        stage=Stage.signed, published_at=datetime.now(timezone.utc),
    )
    base.update(kw)
    p = Professional(**base)
    db.add(p)
    db.commit()
    return p


# ── switched on, and who may use it ─────────────────────────────────────────
def test_status_reports_off_without_a_key(client, configure):
    configure(anthropic_api_key="")
    assert client.get("/api/public/assistant").json() == {"enabled": False, "code_required": True}


def test_a_key_without_a_code_stays_off(client, configure, professions):
    # Forgetting the code must not open a paid endpoint to everyone.
    configure(assistant_access_code="")
    assert client.get("/api/public/assistant").json()["enabled"] is False
    assert ask(client, "Hi").status_code == 503


def test_off_without_a_key(client, configure, professions):
    configure(anthropic_api_key="")
    assert ask(client, "Hi").status_code == 503


@pytest.mark.parametrize("code", [None, "", "wrong", CODE + "x"])
def test_the_preview_needs_the_code(client, configure, professions, code):
    configure()
    assert ask(client, "Hi", code=code).status_code == 401


def test_the_page_can_check_a_code_without_asking_anything(client, configure, professions):
    fake = FakeClient()
    configure(fake=fake)
    check = lambda code: client.post("/api/public/assistant/code",  # noqa: E731
                                     headers={"X-Assistant-Code": code}).status_code
    assert check(CODE) == 204
    assert check("wrong") == 401
    assert fake.requests == []


def test_wrong_codes_are_throttled(client, configure, professions):
    configure()
    statuses = [ask(client, "Hi", code="guess").status_code for _ in range(6)]
    assert statuses == [401] * 5 + [429]


def test_public_mode_needs_no_code(client, configure, professions):
    configure(assistant_public=True, assistant_access_code="")
    assert client.get("/api/public/assistant").json() == {"enabled": True, "code_required": False}
    response = ask(client, "Hi", code=None)
    assert response.status_code == 200
    assert events(response)[-1] == {"type": "done"}


def test_messages_are_rate_limited(client, configure, professions):
    configure(fake=FakeClient(*[answer("Hi.") for _ in range(20)]))
    statuses = [ask(client, "Hi").status_code for _ in range(21)]
    assert statuses == [200] * 20 + [429]


@pytest.mark.parametrize("history", [
    [{"role": "assistant", "content": "Hello"}],
    [{"role": "user", "content": "Hi"}, {"role": "assistant", "content": "Hello"}],
    [{"role": "user", "content": "Hi"}, {"role": "user", "content": "Again"}],
    [{"role": "user", "content": "x" * 2001}],
    [{"role": "user", "content": ""}],
    [],
])
def test_only_a_well_formed_conversation_is_sent(client, configure, professions, history):
    fake = FakeClient()
    configure(fake=fake)
    response = client.post("/api/public/assistant/chat", json={"messages": history},
                           headers={"X-Assistant-Code": CODE})
    assert response.status_code == 422
    assert fake.requests == []


# ── a conversation ──────────────────────────────────────────────────────────
def test_the_answer_streams_to_the_page(client, configure, professions):
    configure(fake=FakeClient(answer("Transfer tax ", "is about 3.09%.")))
    response = ask(client, "What is the transfer tax?")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert events(response) == [
        {"type": "text", "text": "Transfer tax "},
        {"type": "text", "text": "is about 3.09%."},
        {"type": "done"},
    ]


def test_what_is_sent_is_cached_and_has_a_fallback(client, configure, professions):
    fake = FakeClient(answer("Hello."))
    configure(fake=fake)
    ask(client, "Hi", "Hello! How can I help?", "Who are you?")
    [request] = fake.requests

    assert request["model"] == "claude-opus-5-5"
    assert request["output_config"] == {"effort": "low"}
    assert request["fallbacks"] == "default" and request["betas"] == [FALLBACK_BETA]
    # The site's content and the instructions are one cached block; the date
    # rides after it so midnight does not rewrite the cache.
    instructions, today = request["system"]
    assert instructions["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert knowledge() in instructions["text"]
    now = date.today()
    assert today == {"type": "text", "text": f"Today is {now.day} {now:%B %Y}."}
    assert request["cache_control"] == {"type": "ephemeral"}
    # The whole conversation, as the page sent it.
    assert [m["role"] for m in request["messages"]] == ["user", "assistant", "user"]
    # Professions come from the admin's list, in his order.
    search = next(t for t in request["tools"] if t["name"] == "search_professionals")
    assert search["input_schema"]["properties"]["profession"]["enum"] == [
        "agent", "lawyer", "engineer", "architect", "contractor", "property_manager",
        "accountant",
    ]


def test_glm_gets_the_plain_messages_api(client, configure, professions):
    # Z.ai's endpoint speaks Anthropic's Messages API, not Claude's extras.
    fake = FakeClient(answer("Hello."))
    configure(fake=fake, assistant_provider="glm", anthropic_api_key="", glm_api_key="glm-key")
    assert client.get("/api/public/assistant").json()["enabled"] is True
    ask(client, "Hi")
    [request] = fake.requests
    assert request["model"] == GLM_MODEL
    for claude_only in ("betas", "fallbacks", "output_config", "cache_control"):
        assert claude_only not in request
    assert not any("cache_control" in block for block in request["system"])
    assert not any("eager_input_streaming" in tool for tool in request["tools"])
    assert request["thinking"] == {"type": "disabled"}


@pytest.mark.parametrize("settings", [
    {"assistant_provider": "glm"},                        # GLM chosen, no GLM key
    {"assistant_provider": "gpt", "glm_api_key": "k"},    # a provider nobody wrote
])
def test_a_provider_without_its_key_stays_off(client, configure, settings):
    configure(**settings)
    assert client.get("/api/public/assistant").json()["enabled"] is False


def test_glm_is_called_at_zai():
    from app.api.deps import get_assistant_client

    glm = get_assistant_client(Settings(
        _env_file=None, assistant_provider="glm", glm_api_key="k", assistant_access_code=CODE,
    ))
    claude = get_assistant_client(Settings(
        _env_file=None, anthropic_api_key="k", assistant_access_code=CODE,
    ))
    assert str(glm.base_url).rstrip("/") == GLM_BASE_URL
    assert str(claude.base_url).rstrip("/") == "https://api.anthropic.com"


def test_an_empty_filter_is_no_filter(client, configure, db, professions):
    _published(db, professions)
    fake = FakeClient(
        ([], message("tool_use", tool_use("search_professionals",
                                          {"profession": "", "region": "", "language": ""}))),
        answer("Here is everyone."),
    )
    configure(fake=fake)
    ask(client, "Who do you have?")
    [result] = fake.requests[1]["messages"][-1]["content"]
    assert "is_error" not in result
    assert json.loads(result["content"])["matches"] == 1


def test_it_searches_the_directory_and_answers_from_it(client, configure, db, professions):
    _published(db, professions)
    fake = FakeClient(
        ([tool_starts("search_professionals")], message(
            "tool_use", tool_use("search_professionals",
                                 {"profession": "lawyer", "region": "Crete", "language": "English"}))),
        answer("Try [Maria Kyriakou](/p/maria-kyriakou)."),
    )
    configure(fake=fake)
    response = ask(client, "I need an English-speaking lawyer in Crete")

    assert events(response) == [
        {"type": "status", "status": "searching"},
        {"type": "text", "text": "Try [Maria Kyriakou](/p/maria-kyriakou)."},
        {"type": "done"},
    ]
    # The second call carries the tool's answer, linked to the real profile.
    [result] = fake.requests[1]["messages"][-1]["content"]
    found = json.loads(result["content"])
    assert found["matches"] == 1
    assert found["shown"][0]["profile"] == "/p/maria-kyriakou"
    assert "email" not in result["content"] and "m@example.com" not in result["content"]


def test_a_bad_tool_input_is_answered_as_an_error(client, configure, professions):
    fake = FakeClient(
        ([], message("tool_use", tool_use("search_professionals", {"region": "Mars"}))),
        answer("We are not on Mars yet."),
    )
    configure(fake=fake)
    ask(client, "Lawyers on Mars?")
    [result] = fake.requests[1]["messages"][-1]["content"]
    assert result["is_error"] is True
    assert "Mars" in result["content"]


def test_a_refusal_is_said_kindly(client, configure, professions):
    configure(fake=FakeClient(([], message("refusal"))))
    [event] = events(ask(client, "Something it will not do"))
    assert event["type"] == "error" and event["code"] == "refused"


def test_a_failed_call_is_said_kindly(client, configure, professions):
    import anthropic
    import httpx2

    class Failing(FakeClient):
        def _stream(self, **request):
            raise anthropic.APIConnectionError(
                request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages"))

    configure(fake=Failing())
    [event] = events(ask(client, "Hi"))
    assert event == {"type": "error", "code": "failed",
                     "message": "Something went wrong on our side. Please try again."}


# ── the tools see what a visitor sees ───────────────────────────────────────
def test_search_finds_only_published_professionals(db, professions):
    _published(db, professions)
    _published(db, professions, business_name="Hidden", contact_name="Hidden Person",
               slug="hidden-person", published=False)
    found = search_professionals(db, profession="lawyer", region="Crete", language=None)
    assert [p["name"] for p in found["shown"]] == ["Maria Kyriakou"]


def test_search_filters_in_both_directions(db, professions):
    _published(db, professions)
    assert search_professionals(db, profession="agent", region=None, language=None)["matches"] == 0
    assert search_professionals(db, profession=None, region="Athens", language=None)["matches"] == 0
    assert search_professionals(db, profession=None, region=None, language="German")["matches"] == 0
    # "English — Fluent" speaks English.
    assert search_professionals(db, profession=None, region=None, language="english")["matches"] == 1


def test_profile_of_an_unpublished_professional_is_not_found(db, professions):
    _published(db, professions, slug="draft", published=False)
    assert "error" in professional_profile(db, "draft")
    assert professional_profile(db, "maria-kyriakou") == {
        "error": "No published professional has the profile /p/maria-kyriakou."
    }
