# Backend layout

Dependencies point inwards. Nothing in an inner ring imports an outer one.

    api ─────► services ─────► domain
     │             │             ▲
     │             ├──► ports ───┘
     │             └──► repositories ──► models
     └──► routers                          ▲
                   adapters ───────────────┘
                   (implement ports)

## The rings

**`domain/`** — the rules, and nothing else. No FastAPI, no Session, no
settings. `publishing.evaluate()` takes facts and returns a verdict;
`fields.public_values()` decides what a stranger may see. Both are unit tested
in `tests/test_domain.py`, which runs in about a second because it touches
nothing.

`domain/errors.py` is the vocabulary of refusal — `NotFound`, `Conflict`,
`Gone`, `Rejected`. Business rules raise these; they do not know what a status
code is.

**`ports/`** — the interfaces the services depend on: `EmailSender`,
`StorageBackend`, `Clock`. Depending on these rather than on SMTP, Firebase and
`datetime.now()` is what makes the services testable and the adapters
replaceable.

**`adapters/`** — implementations. `email/templates.py` holds the wording and
`email/senders.py` holds the transport, so adding an email never touches SMTP
and swapping SMTP never touches the wording. `InMemoryEmailSender` is a
first-class implementation, which is why the test suite injects rather than
monkeypatches.

**`services/`** — the use cases. One per area, orchestrating repositories,
domain rules and ports. This is where the logic that used to sit inside HTTP
handlers lives.

**`repositories/`** — query construction, only where it is non-trivial.
Deliberately *not* one per model: SQLAlchemy's `Session` is already a unit of
work, and wrapping every `db.get()` would be ceremony. `publishing.py` earns
its place because gathering the publish facts is a real join.

**`api/`** — composition and translation. `deps.py` is the only module that
chooses which adapter is in play. `errors.py` is the only module that knows a
refusal has a status code.

**`routers/`** — HTTP. Read the request, call one service, shape the reply.

## Why it is worth the files

Before this, `agreements.py` was 408 lines with 19 database calls and 22
`HTTPException`s wrapped around OTP and stage logic; `introductions.py` was 419
with rate limiting, consent and email dispatch inline. A rule could only be
exercised through a request, and email could only be swapped by patching a
module global — which the suite had to do to stop it mailing real people.

Now the routers hold no database calls and no status codes, the rules are
tested without a database, and both replacements are ordinary dependency
overrides.

## Where a change goes

| Change | File |
|---|---|
| A new business rule | `domain/` |
| A new email | `adapters/email/templates.py` |
| A different mail provider | a new `EmailSender` in `adapters/email/` |
| A new use case | `services/` |
| A different status code | `api/errors.py` |
| A new endpoint | `routers/`, calling a service |
| A new table | `models/`, plus a migration |
