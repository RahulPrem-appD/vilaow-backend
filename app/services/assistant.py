"""The website assistant: answers a buyer's questions from the site's own
content, and finds professionals in the directory.

What it knows is the website itself. The guides, the Cost Guide and the
how-it-works copy live in the Next.js app, so app/src/lib/assistant-knowledge.ts
renders them into app/content/assistant_knowledge.md and a frontend test fails
whenever that file falls out of step. The whole file goes into the prompt —
about fourteen thousand tokens, small enough that there is nothing to search,
and cached, so a conversation pays for it once rather than on every message.

What it can look up is the directory, through two tools that call the public
router's own functions. That is deliberate: the assistant sees exactly what a
visitor sees, under the same rules — published records only, each field only
when its visibility key allows it. A second query written here would be a
second place for a hidden field to leak from.

The conversation is the browser's. Each request carries the whole thing as
plain text and nothing is stored here, so there is no transcript of what a
visitor typed for anyone to look after. That changes with WhatsApp, which
sends one message at a time and needs the history kept server-side.
"""
from __future__ import annotations

import json
import logging
import random
from collections.abc import Callable, Iterator
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

import anthropic
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Profession
from app.routers import public

log = logging.getLogger("vilaow.assistant")

KNOWLEDGE_FILE = Path(__file__).resolve().parents[1] / "content" / "assistant_knowledge.md"

# Spelled as the homepage's HOME_REGIONS spells them, which is how
# `Professional.region` stores them (app/src/components/home-regions.ts).
REGIONS = ("Athens", "Crete", "Thessaloniki", "Aegean Islands", "Ionian Islands")

# How many professionals one search hands back. Few enough to read in a chat
# bubble; the visitor can ask for more, and the order is random each time.
SEARCH_LIMIT = 4
# Search, read a profile, answer — with one to spare.
MAX_ROUNDS = 4
# A chat reply is a few hundred tokens. The headroom is for thinking, which
# counts against this too; output is billed as used, not as allowed.
MAX_TOKENS = 8000

# Server-side fallback: if the model's safety classifiers decline a request,
# the API re-runs it on the model Anthropic recommends for that category
# instead of handing back a refusal.
FALLBACK_BETA = "server-side-fallback-2026-07-01"

INSTRUCTIONS = """\
You are the assistant on Vilaow's website: an AI that helps people from abroad who are buying property in Greece. Vilaow is a directory of property professionals in Greece — lawyers, real estate agents, civil engineers, architects, contractors and tax advisors — whom the Vilaow team has vetted. Visitors ask you about buying, owning and renting out property in Greece, and about finding the right professional.

Answer from the website's own content, which follows these instructions. When it doesn't cover a question, say so plainly; add only general context you are sure is correct, and suggest which professional can confirm it. Where a guide and the Cost Guide give different figures, use the Cost Guide's. Rules and prices change and every purchase is different, so you give general information, not legal, tax or financial advice: where a decision depends on the visitor's own situation, say that a lawyer or tax advisor should confirm it. Work that into the answer where it matters rather than adding a disclaimer to every reply.

Write for a small chat window: plain words, short paragraphs, a short list only when it really helps, and usually no more than about 120 words. Use **bold** sparingly, and no headings or tables. Link only to pages on this website, as Markdown links whose path starts with "/", such as [the Cost Guide](/costs). When a guide covers the question, end with a link to it. Reply in the language the visitor writes in.

If a question has nothing to do with property in Greece or with Vilaow, say briefly that you can only help with those, and give an example of something you can help with.

Finding professionals:
- Use search_professionals when the visitor wants a professional, or has reached the point where they need one. If you don't know where in Greece they are buying and it matters, ask first.
- Mention only professionals the tool returned, and only what it returned about them. Never invent a name, rating, price or detail. A rating always goes with the source the tool gives for it.
- Present them as equal options. Don't call anyone the best or rank them: nobody can pay Vilaow for a higher place, and everyone listed has been vetted.
- Link each one to their profile, like [Name](/p/slug). On the profile, the visitor presses "Get introduced"; Vilaow introduces them and the professional contacts them directly. It is free for buyers.
- Use get_professional_profile when the visitor wants to know more about someone you found.
- If nobody matches, say so. If the region is not open yet, say it is coming soon. Offer the free 15-minute call with the Vilaow team at /book-a-call.
- You can't see phone numbers or email addresses, book calls or send messages for anyone. Don't offer to.

Don't ask for passport, tax or bank details. If a visitor shares something like that, don't repeat it back.

If someone asks, you are an AI assistant, not a person on the Vilaow team."""


@lru_cache
def knowledge() -> str:
    return KNOWLEDGE_FILE.read_text(encoding="utf-8")


def system_blocks(today: date) -> list[dict[str, Any]]:
    """The instructions and the site's content, then today's date.

    The first block is identical for every visitor, so it carries the cache
    breakpoint; tools render before system, so the breakpoint covers them too.
    An hour rather than five minutes because traffic is light: most
    conversations would otherwise start cold and pay to write it again.

    The date sits after the breakpoint. Inside the cached block it would
    rewrite the whole cache every midnight for the sake of one line.
    """
    return [
        {
            "type": "text",
            "text": f"{INSTRUCTIONS}\n\n<website_content>\n{knowledge()}</website_content>",
            "cache_control": {"type": "ephemeral", "ttl": "1h"},
        },
        {"type": "text", "text": f"Today is {today.day} {today:%B %Y}."},
    ]


def tool_definitions(professions: list[tuple[str, str]]) -> list[dict[str, Any]]:
    """The two directory tools.

    The profession list comes from the database — the owner manages it in the
    admin — so a profession he adds is searchable without a deploy. Order is by
    the admin's position, which keeps the definitions byte-identical between
    requests and the cache intact.

    `eager_input_streaming` lets tool input stream as it is generated; the
    price is that the API no longer validates it, so `_search_input` and
    `_profile_input` do.
    """
    return [
        {
            "name": "search_professionals",
            "description": (
                "Search Vilaow's directory of vetted professionals. Returns how many "
                f"published professionals match and up to {SEARCH_LIMIT} of them, in a "
                "random order so that everyone gets seen, with what their public profile "
                "shows: profile link, profession, title, city, region, languages, years "
                "of experience, badges and rating. Leave out a filter to search more widely."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "profession": {
                        "type": "string",
                        "enum": [key for key, _ in professions],
                        "description": "The kind of professional: "
                        + "; ".join(f"{key} = {label}" for key, label in professions),
                    },
                    "region": {
                        "type": "string",
                        "enum": list(REGIONS),
                        "description": (
                            "Where in Greece. Towns and islands belong to one of these: "
                            "Chania, Heraklion and Rethymno are in Crete; Mykonos and "
                            "Santorini are in the Aegean Islands; Corfu is in the Ionian "
                            "Islands; Athens covers Attica."
                        ),
                    },
                    "language": {
                        "type": "string",
                        "description": "A language the professional should speak, in "
                        "English, such as English, German or French.",
                    },
                },
                "additionalProperties": False,
            },
            "eager_input_streaming": True,
        },
        {
            "name": "get_professional_profile",
            "description": (
                "Read one professional's full public profile on Vilaow: bio, "
                "specialties, areas served, typical costs, FAQ and reviews. Use it "
                "when the visitor asks about someone search_professionals returned."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "slug": {
                        "type": "string",
                        "description": "The part of their profile link after /p/, "
                        "such as kostas-papadopoulos.",
                    },
                },
                "required": ["slug"],
                "additionalProperties": False,
            },
            "eager_input_streaming": True,
        },
    ]


class ToolInputError(ValueError):
    """Input the model sent that the schema would have refused."""


def _search_input(raw: Any, professions: list[tuple[str, str]]) -> dict[str, str | None]:
    if not isinstance(raw, dict) or set(raw) - {"profession", "region", "language"}:
        raise ToolInputError("Expected an object with profession, region and language.")
    profession, region, language = (raw.get(k) for k in ("profession", "region", "language"))
    if profession is not None and profession not in {key for key, _ in professions}:
        raise ToolInputError(f"Unknown profession {profession!r}.")
    if region is not None and region not in REGIONS:
        raise ToolInputError(f"Unknown region {region!r}.")
    if language is not None and not (isinstance(language, str) and 0 < len(language.strip()) <= 40):
        raise ToolInputError("language must be a short name such as English.")
    return {"profession": profession, "region": region,
            "language": language.strip() if language else None}


def _profile_input(raw: Any) -> str:
    slug = raw.get("slug") if isinstance(raw, dict) and set(raw) == {"slug"} else None
    if not (isinstance(slug, str) and 0 < len(slug) <= 120):
        raise ToolInputError("Expected an object with a slug.")
    return slug.strip().removeprefix("/p/")


def _card_summary(card: dict[str, Any]) -> dict[str, Any]:
    """One listing card, as the assistant should read it.

    Every value here came through the public router, so a field the record
    hides is already None and drops out below.
    """
    summary = {
        "name": card["name"],
        "profile": f"/p/{card['slug']}",
        "profession": card.get("profession"),
        "title": card.get("subrole"),
        "city": card.get("city"),
        "region": card.get("region"),
        "languages": card.get("languages"),
        "years_of_experience": card.get("years"),
        "verified_by_vilaow": card.get("verified") or None,
        "badges": card.get("badges"),
    }
    if card.get("rating") is not None:
        summary["rating"] = (
            f"{card['rating']} from {card['review_count']} reviews "
            f"(source: {card['rating_source']})"
        )
    return {key: value for key, value in summary.items() if value not in (None, [], "")}


def search_professionals(db: Session, *, profession: str | None, region: str | None,
                         language: str | None) -> dict[str, Any]:
    # The router's own listing, in a fresh random order — the homepage's rule,
    # so the assistant does not send every buyer to the same few people.
    listing = public.list_professionals(
        region=region, role=profession, city=None, language=None,
        limit=100, offset=0, seed=random.randrange(2_147_483_648), db=db,
    )
    cards = listing["items"]
    matches = listing["total"]
    if language:
        # Prefix rather than exact, unlike the homepage filter: records say
        # "English — Fluent" as well as "English", and both speak English.
        wanted = language.lower()
        cards = [c for c in cards if any(
            spoken.lower().startswith(wanted) for spoken in c.get("languages") or []
        )]
        matches = len(cards)
    return {"matches": matches, "shown": [_card_summary(c) for c in cards[:SEARCH_LIMIT]]}


def professional_profile(db: Session, slug: str) -> dict[str, Any]:
    try:
        profile = public.get_professional(slug=slug, db=db)
    except HTTPException:
        return {"error": f"No published professional has the profile /p/{slug}."}
    data = profile.model_dump(exclude_none=True, exclude={"photo", "initials", "slug"})
    data["profile"] = f"/p/{profile.slug}"
    if profile.reviews:
        data["reviews"] = [
            review.model_dump(exclude_none=True, include={"author", "stars", "text", "source", "verified"})
            for review in profile.reviews[:6]
        ]
    return data


def _replayable(content: list[Any]) -> list[Any]:
    """An assistant turn, safe to send back.

    If a fallback model took over part-way through, the blocks before the
    switch that were not text belong to the model that declined and must not
    be replayed; the API's rule for echoing fallback turns.
    """
    switches = [i for i, block in enumerate(content) if block.type == "fallback"]
    if not switches:
        return content
    last = switches[-1]
    return [b for i, b in enumerate(content) if i >= last or b.type == "text"]


class Assistant:
    def __init__(self, client: anthropic.Anthropic, session_factory: Callable[[], Session], *,
                 model: str, effort: str, today: Callable[[], date] = date.today) -> None:
        self._client = client
        self._session_factory = session_factory
        self._model = model
        self._effort = effort
        self._today = today

    def reply(self, history: list[dict[str, str]]) -> Iterator[dict[str, str]]:
        """Answer the last message, as a stream of events for the page.

        {"type": "status", "status": "searching" | "reading"} while a tool runs,
        {"type": "text", "text": ...} as the answer is written, then exactly one
        {"type": "done"} or {"type": "error", "code": ..., "message": ...}.
        """
        with self._session_factory() as db:
            professions = [(key, label) for key, label in db.execute(
                select(Profession.key, Profession.label)
                .where(Profession.active.is_(True)).order_by(Profession.position)
            )]
        request = dict(
            model=self._model,
            max_tokens=MAX_TOKENS,
            system=system_blocks(self._today()),
            tools=tool_definitions(professions),
            output_config={"effort": self._effort},
            # Caches the conversation so far, on top of the explicit breakpoint
            # on the system prompt: the next message reads it back.
            cache_control={"type": "ephemeral"},
            betas=[FALLBACK_BETA],
            fallbacks="default",
        )
        messages: list[dict[str, Any]] = [
            {"role": m["role"], "content": m["content"]} for m in history
        ]

        for _ in range(MAX_ROUNDS):
            try:
                response = yield from self._stream(request, messages)
            except anthropic.RateLimitError:
                log.warning("assistant: rate limited by Anthropic")
                yield _error("busy", "A lot of people are asking right now. Please try again in a minute.")
                return
            except anthropic.APIStatusError as problem:
                log.error("assistant: Anthropic returned %s (request %s)",
                          problem.status_code, problem.request_id)
                yield _error("failed", "Something went wrong on our side. Please try again.")
                return
            except anthropic.APIConnectionError:
                log.error("assistant: could not reach Anthropic")
                yield _error("failed", "Something went wrong on our side. Please try again.")
                return
            if response is None:
                yield _error("failed", "Something went wrong on our side. Please try again.")
                return

            if response.stop_reason == "refusal":
                yield _error("refused", "Sorry, I can't help with that one. Try asking "
                             "another way, or book a free call with our team.")
                return
            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if response.stop_reason != "tool_use" or not tool_uses:
                # A truncated answer is still an answer; it just ends early.
                yield {"type": "done"}
                return

            messages.append({"role": "assistant", "content": _replayable(response.content)})
            messages.append({"role": "user", "content": [
                self._run_tool(block, professions) for block in tool_uses
            ]})

        yield _error("too_many_steps",
                     "That took more steps than I can manage. Please try asking more simply.")

    def _stream(self, request: dict[str, Any], messages: list[dict[str, Any]]):
        """One model call, its text forwarded as it arrives. Returns the final
        message, or None when its tool input could not be parsed twice running."""
        for attempt in range(2):
            try:
                with self._client.beta.messages.stream(**request, messages=messages) as stream:
                    for event in stream:
                        if event.type == "content_block_start" and event.content_block.type == "tool_use":
                            yield {"type": "status", "status": _status(event.content_block.name)}
                        elif event.type == "text":
                            yield {"type": "text", "text": event.text}
                    response = stream.get_final_message()
            except ValueError:
                # Tool input so malformed the SDK could not parse it at all.
                # There is no tool_use id to answer, so ask again.
                log.warning("assistant: unparseable tool input (attempt %d)", attempt + 1)
                continue
            usage = response.usage
            log.info("assistant: model=%s stop=%s input=%s cache_read=%s cache_write=%s output=%s",
                     response.model, response.stop_reason, usage.input_tokens,
                     usage.cache_read_input_tokens, usage.cache_creation_input_tokens,
                     usage.output_tokens)
            return response
        return None

    def _run_tool(self, block: Any, professions: list[tuple[str, str]]) -> dict[str, Any]:
        try:
            with self._session_factory() as db:
                if block.name == "search_professionals":
                    result = search_professionals(db, **_search_input(block.input, professions))
                elif block.name == "get_professional_profile":
                    result = professional_profile(db, _profile_input(block.input))
                else:
                    raise ToolInputError(f"There is no tool called {block.name}.")
        except ToolInputError as problem:
            return {"type": "tool_result", "tool_use_id": block.id, "is_error": True,
                    "content": str(problem)}
        return {"type": "tool_result", "tool_use_id": block.id,
                "content": json.dumps(result, ensure_ascii=False, default=str)}


def _status(tool_name: str) -> str:
    return "reading" if tool_name == "get_professional_profile" else "searching"


def _error(code: str, message: str) -> dict[str, str]:
    """`code` is for the page, which words the error in the visitor's language;
    `message` is the English for anything that shows text as it comes."""
    return {"type": "error", "code": code, "message": message}
