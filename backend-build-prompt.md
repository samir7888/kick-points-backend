# Build Prompt: Kick Prediction Auto-Voter — FastAPI Backend (SaaS)

> **How to use:** put your existing Python code in a `legacy/` folder in the repo, open the repo in your AI IDE (Cursor, Claude Code, Windsurf, etc.), and paste this whole file as the first message (or save it as `docs/BUILD_PROMPT.md` and tell the AI to follow it). Work milestone by milestone (Section 19).

---

## 1. Role and objective

You are a senior Python backend engineer. Build the **production-ready FastAPI backend** for a SaaS product that automatically participates in Kick.com channel **predictions** on behalf of signed-in users.

I already have working Python code for **detecting predictions and placing votes**. It lives in `legacy/`. **Your job is to wrap it in a multi-tenant backend, not to rewrite it.** Frontend (Next.js) comes later; build **backend only** now, but design the API so a Next.js app can consume it.

## 2. Product summary

- Users sign in with **"Sign in with Kick"** (OAuth 2.1 + PKCE). No pasted tokens.
- A user adds Kick channels. The system watches those channels for predictions and votes automatically using the user's own account.
- The user can set a **different vote amount per channel**, and view **past predictions per channel**, including their own vote and its outcome.
- Plans (monthly):
  - **Free:** 1 channel.
  - **Premium:** up to **6 channels**, **$1 / month**, paid through **Dodo Payments**.
- Identity is the numeric **`kick_user_id`** (unique). Never use the username as an identifier.

## 3. Scope

**In scope (this task):** FastAPI app, PostgreSQL schema + migrations, auth, channel/settings/prediction/vote APIs, Dodo billing, background workers (listener, vote, settle, expiry, canary), SSE stream, admin kill switch, tests, Docker Compose for local dev, README.

**Out of scope:** the Next.js frontend, email sending (leave an interface + log-only implementation), mobile apps, any payment provider other than Dodo.

## 4. Non-negotiable rules

1. **Do not invent external API details.** For Kick and Dodo, use only endpoints, fields, headers, and event names you can confirm from their official docs or from the `legacy/` code. If something is unknown, define an interface, put a clearly marked `# TODO(verify): ...` comment, make the value configurable, and list it in your final report. Never fabricate a URL or field.
2. **Do not rewrite or "improve" the logic in `legacy/`.** Wrap it behind the adapter interfaces in Section 10. You may make minimal changes only to make it importable and async-safe, and you must list each change.
3. **All plan limits and permissions are enforced server-side**, in one place, never trusting the client.
4. **Idempotency everywhere money or votes are involved** (webhooks, vote placement, payment crediting).
5. **Secrets never appear in code, logs, error messages, or API responses.** OAuth tokens are encrypted at rest.
6. **Every endpoint is covered by at least one test** and the test suite must pass before you call a milestone done.
7. If a requirement is ambiguous and **blocking**, ask me one concise batch of questions. Otherwise make a reasonable assumption, state it, and continue.
8. Prefer small, reviewable commits with clear messages (one per logical step).

## 5. Tech stack (use these unless you can justify a change)

- Python **3.12**, package manager **uv** (fall back to pip + `requirements.txt` if needed)
- **FastAPI** + **Uvicorn**, **Pydantic v2** + **pydantic-settings**
- **SQLAlchemy 2.0 (async)** + **asyncpg**, **Alembic** migrations
- **PostgreSQL 16**, **Redis 7** (pub/sub for SSE, locks, rate limits, job queue)
- **arq** for background jobs (Redis-backed)
- **httpx** (async) for outbound HTTP
- **cryptography** (`MultiFernet`) for token encryption
- **structlog** (JSON logs), **prometheus-client** for metrics
- Tests: **pytest**, **pytest-asyncio**, **httpx.AsyncClient**, a real Postgres + Redis in tests (docker compose or testcontainers), **respx** to mock outbound HTTP
- Lint/format: **ruff**; type-check: **mypy** (strict on `app/`, lenient on `legacy/`)

## 6. Repository layout

```
.
├── app/
│   ├── main.py                  # app factory, routers, middleware, lifespan
│   ├── core/
│   │   ├── config.py            # pydantic-settings, all env vars
│   │   ├── security.py          # sessions, CSRF/origin check, token encryption
│   │   ├── errors.py            # error codes + exception handlers
│   │   ├── logging.py
│   │   └── rate_limit.py
│   ├── db/
│   │   ├── base.py, session.py
│   │   └── models/              # one file per aggregate
│   ├── schemas/                 # Pydantic request/response models
│   ├── api/v1/                  # routers: auth, me, channels, settings, predictions,
│   │                            #          votes, billing, webhooks, stream, admin, health
│   ├── services/                # business logic (no FastAPI imports)
│   │   ├── plans.py             # limits, is_premium, reconcile_channels
│   │   ├── channels.py, predictions.py, votes.py, billing.py, auth.py
│   ├── integrations/
│   │   ├── kick/
│   │   │   ├── gateway.py       # KickGateway Protocol (the ONLY thing the app depends on)
│   │   │   ├── legacy_adapter.py# wraps ./legacy code
│   │   │   ├── oauth.py         # PKCE login + token refresh/revoke
│   │   │   ├── fake.py          # FakeKickGateway for tests/dev
│   │   │   └── models.py        # normalized PredictionEvent, Outcome, etc.
│   │   └── dodo/
│   │       └── client.py        # checkout, portal, webhook verification
│   └── workers/
│       ├── listener.py          # channel listener manager (own process)
│       ├── tasks.py             # arq tasks: vote, settle, expiry, canary
│       └── settings.py          # arq WorkerSettings
├── legacy/                      # my existing code (do not restructure)
├── migrations/                  # Alembic
├── tests/
├── docker-compose.yml           # postgres, redis, api, worker, listener
├── Dockerfile
├── .env.example
└── README.md
```

## 7. Configuration (`.env.example` must list all of these)

```
APP_ENV=dev|prod
SECRET_KEY=                      # session signing
TOKEN_ENCRYPTION_KEYS=           # comma-separated Fernet keys; first encrypts, all decrypt (rotation)
DATABASE_URL=postgresql+asyncpg://...
REDIS_URL=redis://...
API_URL=https://api.example.com
SITE_URL=https://app.example.com
CORS_ORIGINS=https://app.example.com
COOKIE_DOMAIN=
ADMIN_KICK_USER_IDS=             # comma-separated kick_user_id values with admin rights

# Kick (TODO(verify) every default against Kick's official docs; keep ALL configurable)
KICK_CLIENT_ID=
KICK_CLIENT_SECRET=
KICK_AUTHORIZE_URL=https://id.kick.com/oauth/authorize
KICK_TOKEN_URL=https://id.kick.com/oauth/token
KICK_REVOKE_URL=https://id.kick.com/oauth/revoke
KICK_API_BASE=https://api.kick.com/public/v1
KICK_SCOPES=                     # space-separated; what the legacy vote path actually needs
KICK_REDIRECT_URI=${API_URL}/api/v1/auth/kick/callback

# Dodo Payments
DODO_ENV=test|live
DODO_API_KEY=
DODO_WEBHOOK_SECRET=
DODO_PREMIUM_PRODUCT_ID=         # the $1/month subscription product created in the Dodo dashboard
PREMIUM_PRICE_DISPLAY=1       # display only; entitlement NEVER depends on price math

# Product rules
FREE_CHANNEL_LIMIT=1
PREMIUM_CHANNEL_LIMIT=6
MIN_VOTE_AMOUNT=10               # platform minimum points per vote
PAST_DUE_GRACE_DAYS=3
MIN_SECONDS_BEFORE_CLOSE=5
VOTE_JITTER_MAX_SECONDS=2
```

## 8. Domain rules

- `channel_limit(user)` = `PREMIUM_CHANNEL_LIMIT` if premium else `FREE_CHANNEL_LIMIT`.
- `is_premium(user)` = `premium_until` is in the future **or** within `PAST_DUE_GRACE_DAYS` after a failed renewal.
- **Adding a channel:** lock the user row (`SELECT ... FOR UPDATE`), count existing channels, reject with HTTP **402** and code `PLAN_LIMIT` if at the limit. This must be safe against two simultaneous requests.
- **Downgrade / expiry:** never delete channels. `reconcile_channels(user)` keeps the **oldest N** channels active (N = current limit) and sets `paused_reason='plan_limit'` on the rest. On upgrade it clears `plan_limit` pauses automatically. Call it after every billing change and from a daily job.
- **Per-channel amount:** `channels.vote_amount` (nullable, integer, `>= MIN_VOTE_AMOUNT`). Effective amount = `channel.vote_amount` or `user_settings.default_vote_amount`.
- **Insufficient balance:** if balance < effective amount and `fallback_to_min` is true, vote `MIN_VOTE_AMOUNT` when balance allows it; otherwise record the vote as `skipped` with reason `INSUFFICIENT_POINTS`.
- **Timing:** skip (status `skipped`, reason `TOO_LATE`) if the prediction closes in less than `MIN_SECONDS_BEFORE_CLOSE`. Add random jitter up to `VOTE_JITTER_MAX_SECONDS` before voting.
- **One vote per user per prediction**, enforced by a database unique constraint.
- **Free-tier abuse:** `users.kick_user_id` is `UNIQUE NOT NULL`. Login does an atomic upsert (`INSERT ... ON CONFLICT (kick_user_id) DO UPDATE`).

## 9. Data model (SQLAlchemy models + one Alembic migration per milestone)

Use `BIGINT` ids where Kick ids are involved, `TIMESTAMPTZ` everywhere, and sensible indexes.

| Table            | Columns / constraints                                                                                                                                                                                                                                                                         |
| ---------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `users`          | id PK, **kick_user_id BIGINT UNIQUE NOT NULL**, username, email NULL, premium_until NULL, is_admin bool default false, created_at, last_login_at, deleted_at NULL                                                                                                                             |
| `sessions`       | id PK (store a **hash** of the random session token), user_id FK, expires_at, user_agent, ip, created_at                                                                                                                                                                                      |
| `oauth_states`   | state PK, code_verifier, created_at, expires_at (short TTL, single use)                                                                                                                                                                                                                       |
| `kick_tokens`    | user_id PK/FK, access_token_enc, refresh_token_enc, expires_at, scopes, health enum(`ok`,`needs_reauth`), updated_at                                                                                                                                                                          |
| `user_settings`  | user_id PK/FK, default_vote_amount int (>= MIN), fallback_to_min bool default true, paused bool default false                                                                                                                                                                                 |
| `channels`       | id PK, user_id FK, channel_slug, kick_channel_id BIGINT NULL, vote_amount int NULL, enabled bool default true, paused_reason enum(`user`,`plan_limit`,`token`) NULL, created_at; **UNIQUE(user_id, lower(channel_slug))**                                                                     |
| `predictions`    | id PK, channel_slug, kick_prediction_id, title, outcomes JSONB, state enum(`open`,`locked`,`resolved`,`cancelled`), winning_outcome_id NULL, opened_at, closes_at NULL, resolved_at NULL; **UNIQUE(channel_slug, kick_prediction_id)**; **INDEX(channel_slug, opened_at DESC, id DESC)**      |
| `votes`          | id PK, user_id FK, channel_id FK, prediction_id FK, outcome_id, amount_requested, amount_placed NULL, status enum(`pending`,`placed`,`won`,`lost`,`refunded`,`skipped`,`failed`), reason NULL, error NULL, points_delta NULL, created_at, settled_at NULL; **UNIQUE(user_id, prediction_id)** |
| `subscriptions`  | id PK, user_id FK, dodo_subscription_id UNIQUE, dodo_customer_id, status, current_period_end, cancel_at_period_end bool, updated_at                                                                                                                                                           |
| `webhook_events` | id PK = provider webhook id (UNIQUE), type, payload JSONB, received_at, processed_at NULL, error NULL                                                                                                                                                                                         |
| `audit_log`      | id, user_id NULL, action, detail JSONB, ip, created_at                                                                                                                                                                                                                                        |

Predictions are public per channel; **votes are private** and must always be filtered by the authenticated `user_id`.

## 10. Kick integration layer

The app depends **only** on this Protocol (`app/integrations/kick/gateway.py`). Everything else about Kick lives behind it.

```python
class KickGateway(Protocol):
    async def resolve_channel(self, slug: str, token: str) -> ChannelInfo: ...
    async def get_active_prediction(
        self, slug: str, token: str
    ) -> PredictionEvent | None: ...
    async def get_points_balance(self, slug: str, token: str) -> int | None: ...
    async def place_vote(
        self, slug: str, prediction_id: str, outcome_id: str, amount: int, token: str
    ) -> VoteResult: ...
    def listen(
        self, slug: str
    ) -> AsyncIterator[PredictionEvent]: ...  # realtime stream with REST fallback
```

- `legacy_adapter.py` implements it by **calling my existing code in `legacy/`**. Read `legacy/` first and map its functions onto this Protocol. Show me the mapping before implementing.
- Vote-choice logic also lives in `legacy/`. Wrap it as `VoteStrategy.choose_outcome(prediction, settings) -> outcome_id | None` (returns `None` = don't vote).
- `fake.py` implements the same Protocol deterministically for tests and local dev (scripted predictions, controllable failures).
- All responses are validated with Pydantic. **A shape mismatch must raise `KickSchemaError`, never silently continue.**
- Wrap calls with timeouts, retries with exponential backoff + jitter (never retry `place_vote` blindly; see Section 14), and a **circuit breaker**. 401/403 from Kick marks the user's token `needs_reauth`; repeated 403/429 across users trips the global breaker.
- Token refresh happens before expiry, guarded by a **per-user Redis lock** so two workers never refresh the same token at once.

## 11. Auth (Sign in with Kick)

- `GET /auth/kick/login` → create `state` + PKCE `code_verifier`, store in `oauth_states`, redirect to Kick.
- `GET /auth/kick/callback` → validate `state` (single use, not expired), exchange the code, fetch the Kick user, **atomic upsert by `kick_user_id`**, store encrypted tokens, create a session, set the cookie, redirect to `SITE_URL`.
- Session: opaque random token in an **HttpOnly, Secure, SameSite=Lax** cookie; only its hash is stored. Sliding expiry (e.g. 30 days). Logout deletes the row.
- State-changing requests (POST/PATCH/PUT/DELETE) must pass an **Origin/Referer check** against `CORS_ORIGINS` (CSRF defence).
- `DELETE /me/kick-connection` revokes tokens at Kick (best effort), deletes them locally, and pauses all channels with `paused_reason='token'`.
- `DELETE /me` soft-deletes the user, revokes tokens, removes personal data, and cancels any active Dodo subscription.

## 12. API specification (`/api/v1`, JSON, session cookie auth unless noted)

**Error format** for all errors: `{"error": {"code": "PLAN_LIMIT", "message": "...", "details": {}}}`. Use proper status codes (401, 403, 404, 409, 422, 402 for plan limits, 429, 503 when voting is globally paused).

**Auth / profile**

| Method | Path                  | Notes                                                                                                          |
| ------ | --------------------- | -------------------------------------------------------------------------------------------------------------- |
| GET    | `/auth/kick/login`    | public, redirect                                                                                               |
| GET    | `/auth/kick/callback` | public, redirect                                                                                               |
| POST   | `/auth/logout`        |                                                                                                                |
| GET    | `/me`                 | `{id, kick_user_id, username, plan, premium_until, channel_limit, channels_used, token_health, voting_paused}` |
| DELETE | `/me/kick-connection` |                                                                                                                |
| DELETE | `/me`                 |                                                                                                                |

**Channels**

| Method | Path                | Notes                                                                                                                                   |
| ------ | ------------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| GET    | `/channels`         | each item: id, slug, enabled, paused_reason, vote_amount, effective_amount, live (cached), active_prediction summary                    |
| POST   | `/channels`         | body `{slug, vote_amount?}`; slug validated (`^[A-Za-z0-9_-]{1,50}$`); resolves channel via gateway; 402 `PLAN_LIMIT`; 409 on duplicate |
| PATCH  | `/channels/{id}`    | body `{vote_amount?, enabled?}`; `vote_amount: null` resets to the default; `enabled: true` on a `plan_limit` pause → 402               |
| PUT    | `/channels/amounts` | bulk `{items:[{id, vote_amount}]}`, atomic                                                                                              |
| DELETE | `/channels/{id}`    | history is kept                                                                                                                         |

**Predictions and votes (per channel history is a core feature)**

| Method | Path                                         | Notes                                                                       |
| ------ | -------------------------------------------- | --------------------------------------------------------------------------- | -------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| GET    | `/channels/{id}/predictions`                 | query: `limit` (≤100, default 20), `cursor`, `status` (`open                | resolved | cancelled`), `voted`(bool). Cursor = opaque base64 of`(opened_at, id)`. Each item: prediction fields + **my vote** (or `null`) with outcome, amount, status, points_delta. Response `{items, next_cursor}`. Only the channel owner (the user) can query. |
| GET    | `/channels/{id}/predictions/{prediction_id}` | full outcomes + my vote                                                     |
| GET    | `/channels/{id}/stats`                       | votes placed, won, lost, win rate, net points, last vote time               |
| GET    | `/predictions/active`                        | open predictions across my channels                                         |
| GET    | `/votes`                                     | my votes across channels; filters `channel_id`, `status`; cursor pagination |

History only exists from when the channel was first enabled; document this in the OpenAPI description.

**Settings**

| Method  | Path        | Notes                                                                                      |
| ------- | ----------- | ------------------------------------------------------------------------------------------ |
| GET/PUT | `/settings` | `{default_vote_amount, fallback_to_min, paused}`; `default_vote_amount >= MIN_VOTE_AMOUNT` |

**Billing (Dodo only)**

| Method | Path                | Notes                                                                                                                              |
| ------ | ------------------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| POST   | `/billing/checkout` | creates a Dodo checkout session for `DODO_PREMIUM_PRODUCT_ID`; put our `user_id` in the session metadata; returns `{checkout_url}` |
| POST   | `/billing/portal`   | returns `{portal_url}` for the Dodo customer portal                                                                                |
| GET    | `/billing/status`   | `{plan, premium_until, cancel_at_period_end, status, price_display}`                                                               |
| POST   | `/webhooks/dodo`    | public; signature-verified; see Section 13                                                                                         |

**Realtime / ops**

| Method | Path                 | Notes                                                                                                                                                                                    |
| ------ | -------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| GET    | `/stream`            | Server-Sent Events for the current user (prediction opened/resolved, vote placed/failed, token needs reauth). Backed by Redis pub/sub channel `user:{id}`. Heartbeat comment every 15 s. |
| GET    | `/healthz`           | liveness                                                                                                                                                                                 |
| GET    | `/readyz`            | checks DB + Redis                                                                                                                                                                        |
| GET    | `/metrics`           | Prometheus (restrict by network/auth)                                                                                                                                                    |
| POST   | `/admin/kill-switch` | admin only; body `{enabled: bool}`; stops all voting instantly (Redis flag + DB)                                                                                                         |
| GET    | `/admin/overview`    | admin only; counts of users, premium users, listeners, queue lag, breaker state                                                                                                          |

## 13. Billing with Dodo Payments

- Use the **official Dodo Python SDK if one exists**, otherwise `httpx`. **Read Dodo's current docs first** (start at `https://docs.dodopayments.com/llms.txt`) and follow them for: creating checkout sessions, the customer portal, the webhook signature scheme, and the exact event names. Do not guess.
- The $1/month price is set on the **Dodo product**, not in code. The backend only needs the product id.
- **Webhook handler rules:**
  1. Read the **raw request body** before any JSON parsing.
  2. Verify the signature; on failure return **401** and do nothing.
  3. Insert the provider webhook id into `webhook_events` with a unique constraint; if it already exists, return 200 (duplicate delivery).
  4. Process in one DB transaction, then mark `processed_at`. Return 200 fast; heavy work goes to arq.
  5. Find the user via the `user_id` metadata (fall back to `dodo_customer_id`).
- **Status mapping** lives in **one** function. Names below are placeholders: `TODO(verify)` each against Dodo's docs.

| Dodo event (verify names)             | Effect                                                                     |
| ------------------------------------- | -------------------------------------------------------------------------- |
| subscription active / renewed         | status `active`; `premium_until = current_period_end`                      |
| payment succeeded                     | extend/confirm `premium_until`                                             |
| subscription on hold / payment failed | status `past_due`; keep access for `PAST_DUE_GRACE_DAYS`                   |
| subscription cancelled                | `cancel_at_period_end = true`; access continues until `current_period_end` |
| subscription expired                  | access ends; no extension                                                  |

- After **every** applied event call `reconcile_channels(user)`; a daily `expiry` job does the same for lapsed users.
- Never trust the browser redirect after checkout to grant premium; only verified webhooks (plus an optional "refresh status" call to Dodo) do.
- Test with Dodo **test mode** first; the README must explain how to run a webhook locally (tunnel) and how to replay events.

## 14. Background processes (three separate entrypoints, same image)

1. **api**: `uvicorn app.main:app`
2. **worker**: `arq app.workers.settings.WorkerSettings`: tasks `vote_task`, `settle_task`, `expiry_task`, `canary_task`, `webhook_retry_task`
3. **listener**: `python -m app.workers.listener`

**Listener manager**

- Every 30 s, compute the distinct set of channels that have at least one **enabled, un-paused, entitled** channel row whose user has `token health = ok` and `settings.paused = false`. Start listeners for new channels, stop idle ones.
- Run under a **Postgres advisory lock** so only one manager is active across replicas.
- Each listener uses `gateway.listen(slug)` (realtime) with a periodic REST poll as fallback and reconnects with backoff.
- On a new/updated prediction event: upsert into `predictions`, publish to Redis, and **enqueue one `vote_task` per eligible user** (with jitter). On `resolved/cancelled`: update the prediction and enqueue `settle_task`.

**Vote task (the critical path)**

1. If the global kill switch is on or the breaker is open → exit (record `skipped`/`VOTING_PAUSED`).
2. `INSERT INTO votes (...) status='pending' ON CONFLICT (user_id, prediction_id) DO NOTHING RETURNING id`. **If nothing is returned, stop**: another worker already handled it.
3. Run `VoteStrategy` → outcome (or skip), compute the effective amount (Section 8), call `gateway.place_vote`.
4. Update the row to `placed` / `failed` / `skipped` with a reason; publish an SSE event.

- **Never auto-retry a vote that may already have been placed.** A `pending` vote older than 2 minutes is reconciled by _checking the platform_ first (if the gateway can), otherwise marked `failed` with reason `UNKNOWN_OUTCOME`. Retries are allowed only for failures that definitely happened before placement (e.g. connection refused, token refresh needed).

**Settle task:** when a prediction resolves, set each placed vote to `won` / `lost` / `refunded` using `winning_outcome_id`; fill `points_delta` if the gateway provides it, else leave null.

**Canary task (every 5 min):** a harmless read call with a configured test account. Failure feeds the circuit breaker, alerts, and surfaces on `/admin/overview`.

## 15. Security requirements

- Encrypt tokens with `MultiFernet` (rotation supported); decrypt only at the moment of use; never log them. Add a log filter that redacts anything that looks like a token or `Authorization` header.
- Strict **CORS** (explicit origins, credentials allowed), security headers, request size limits.
- **Rate limiting** (Redis): per IP on auth endpoints, per user on mutations, stricter on `/billing/*` and `/channels` POST.
- Validate every input with Pydantic; slugs by regex; amounts by range.
- Ownership checks on **every** `{id}` route (return 404, not 403, for resources of other users).
- Admin routes require `is_admin` (seeded from `ADMIN_KICK_USER_IDS`).
- Write to `audit_log` for: login, logout, channel add/remove/amount change, token disconnect, billing changes, kill switch.
- Dependencies pinned; run `pip-audit`/`uv` audit in CI.

## 16. Observability and resilience

- Structured JSON logs with a request id and user id (never PII beyond ids).
- Prometheus metrics: HTTP latency/status, Kick call count/latency/status **per operation**, vote outcomes, queue depth, listener count, breaker state.
- Graceful shutdown for listener and worker (finish in-flight votes, cancel tasks cleanly).
- A user-visible `voting_paused` flag in `/me` and the SSE stream when the kill switch or breaker is active, so the UI can say "Kick API issue, votes paused."

## 17. Testing requirements

Write tests for at least:

1. **Auth:** login upsert creates one user per `kick_user_id`; second login with a changed username updates, doesn't duplicate; invalid/expired/reused `state` rejected.
2. **Plan limits:** free user can add 1 channel, 2nd → 402 `PLAN_LIMIT`; premium can add 6, 7th → 402; **two concurrent adds at the limit** don't both succeed.
3. **Downgrade/upgrade:** expiry pauses all but the oldest channel (nothing deleted); resubscribe resumes them.
4. **Per-channel amount:** validation (`< MIN` rejected), `null` resets to default, effective amount used by the vote task, fallback and `INSUFFICIENT_POINTS` paths.
5. **Predictions history:** pagination correctness (no duplicates/gaps across pages), status filter, user A cannot see user B's votes or channels, ordering newest first.
6. **Votes:** the same event delivered twice produces one vote; kill switch blocks voting; `TOO_LATE` skip; settle sets won/lost correctly.
7. **Dodo webhooks:** bad signature → 401; duplicate webhook id → processed once; each mapped event updates `premium_until`/status and triggers `reconcile_channels`.
8. **Kick resilience:** schema mismatch raises `KickSchemaError` and pauses voting; 401 marks `needs_reauth`; breaker opens after repeated failures.
9. **Ops:** `/healthz`, `/readyz`, admin routes reject non-admins.

Use `FakeKickGateway` and `respx` for outbound calls. Aim for meaningful coverage of `services/` and `api/`.

## 18. Local dev and deployment

- `docker-compose.yml`: `postgres`, `redis`, `api`, `worker`, `listener` (hot reload for api in dev).
- `make` (or `justfile`) targets: `up`, `down`, `migrate`, `test`, `lint`, `seed`.
- `seed` script creates a test user, a fake premium user, and a `FakeKickGateway` scenario so I can exercise the whole flow without Kick.
- README: setup, env vars, how to run each process, how to add a migration, how to test Dodo webhooks locally, and a short operations runbook (kill switch, rotating encryption keys, what to do when the canary fails).

## 19. Milestones (finish, test, and report each before starting the next)

| #   | Deliverable                                                                           | Acceptance criteria                                                                                 |
| --- | ------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------- |
| M0  | Scaffold: app factory, config, logging, errors, docker-compose, CI config, `/healthz` | `docker compose up` works; lint + a smoke test pass                                                 |
| M1  | DB models + Alembic migration for all tables in Section 9                             | Migration applies/rolls back cleanly; unique constraints verified by tests                          |
| M2  | Sign in with Kick, sessions, `/me`, disconnect/delete                                 | Tests in Section 17 #1 pass; works end-to-end with a mocked Kick OAuth                              |
| M3  | Channels + settings + plan limits + per-channel amounts + `reconcile_channels`        | Tests #2, #3, #4 pass, including the concurrency test                                               |
| M4  | Kick adapter over `legacy/` + `FakeKickGateway`; listener, vote, settle, canary tasks | Full fake-gateway scenario runs: prediction opens → votes placed once per user → resolves → settled |
| M5  | Prediction history, stats, votes list, SSE, active predictions                        | Test #5 passes; SSE delivers events in an integration test                                          |
| M6  | Dodo checkout, portal, webhooks, status, expiry job                                   | Test #7 passes; documented manual test in Dodo test mode                                            |
| M7  | Hardening: rate limits, audit log, admin kill switch/overview, metrics, breaker       | Tests #8, #9 pass                                                                                   |
| M8  | README + OpenAPI polish + final report                                                | A new developer can run everything from the README alone                                            |

## 20. Working rules and final report

- Start by **reading `legacy/` and replying with**: (a) a short summary of what it does, (b) the mapping to the `KickGateway` Protocol and `VoteStrategy`, (c) anything missing or unsafe you noticed, (d) your plan for M0–M1. Then proceed.
- After each milestone, report: what was built, tests added and their result, assumptions made, `TODO(verify)` items, and anything you need from me.
- **Final report (after M8):** list every `TODO(verify)` (especially Kick URLs/scopes and Dodo event names/signature scheme), every change made inside `legacy/`, known limitations, and the exact commands to run the system and the tests.

**Definition of done:** all tests pass, `docker compose up` brings up api + worker + listener against the fake gateway, every endpoint in Section 12 exists and is documented in OpenAPI, plan limits are enforced server-side under concurrency, Dodo webhooks are verified and idempotent, and no secret appears in logs or responses.
