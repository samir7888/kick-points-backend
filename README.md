# Kick Prediction Backend

A FastAPI backend for a SaaS product that automatically participates in Kick.com channel predictions on behalf of signed-in users.

## Features

- **Multi-tenant architecture** with "Sign in with Kick" OAuth 2.1 + PKCE
- **Automatic prediction voting** using smart strategies from legacy code
- **Tiered plans**: Free (1 channel) and Premium ($1/month, 6 channels)
- **Real-time WebSocket** prediction monitoring via Kick's Pusher API
- **Manual QR / bank-transfer billing**: users scan a QR, upload a proof, and an admin confirms Premium
- **Points-growth analytics** dashboard backed by 15-minute balance snapshots
- **Background workers** for voting, settling, canary health checks, and balance snapshots
- **Server-Sent Events** for real-time UI updates
- **Admin controls** including global kill switch
- **Production ready** with structured logging, metrics, and Docker deployment

## Quick Start

### Prerequisites

- Docker and Docker Compose
- Python 3.12+ (for local development)

### 1. Configure the environment

```bash
cp .env.example .env
```

Create a Fernet key and set `TOKEN_ENCRYPTION_KEYS` to it. Configure the Kick OAuth
client and the registered OAuth redirect URI. The Kick app must include the scopes
required by the legacy voting integration.

### 2. Start infrastructure and migrate

```bash
docker compose up -d postgres redis
make migrate
```

### 3. Start API and background processes

```bash
docker compose up -d api worker listener
```

The API is available at `http://localhost:8000`; `/healthz` is liveness and
`/readyz` checks the database and Redis.
`MAX_REQUEST_BODY_BYTES` limits buffered request bodies (default: 1 MiB), including
chunked requests without a `Content-Length` header.

## Environment Variables

All environment variables are documented in `.env.example`. Key variables:

### Required

- `SECRET_KEY`: Session signing key (32+ characters)
- `TOKEN_ENCRYPTION_KEYS`: Fernet keys for OAuth token encryption
- `DATABASE_URL`: PostgreSQL connection string
- `REDIS_URL`: Redis connection string
- `KICK_CLIENT_ID` / `KICK_CLIENT_SECRET`: Kick.com OAuth credentials
- `PAYMENTS_ADMIN_PASSWORD`: password-protecting the `/payments` review page

### Important Notes

- Kick OAuth PKCE S256 and current-user lookup follow their current official
  documentation.
- `KICK_SCOPES` remains deployment-specific: `user:read` is needed for current-user
  lookup. Kick's documented public scopes do not include prediction voting; the
  legacy adapter's prediction read/vote endpoints and Pusher subscription remain
  unverified and must be confirmed before production use.
- The legacy code has no verified points-balance operation, so the adapter cannot
  apply the minimum-points fallback when the balance is unknown.
- Premium is sold as "$1/month" (display only). Payment is collected out of band
  via the QR / bank-transfer flow; set `PAYMENTS_ADMIN_PASSWORD` and review proofs
  on the `/payments` page.
- Encryption keys support rotation (comma-separated, first encrypts, all decrypt).
  Never reuse the example key in production.

## Architecture

```
┌─────────────┐    ┌──────────────┐    ┌─────────────┐
│   Next.js   │────│  FastAPI     │────│ PostgreSQL  │
│  Frontend   │    │   Backend    │    │  Database   │
└─────────────┘    └──────────────┘    └─────────────┘
                          │                     │
                   ┌──────────────┐    ┌─────────────┐
                   │    Redis     │────│   Workers   │
                   │ Pub/Sub+Jobs │    │ Vote+Listen │
                   └──────────────┘    └─────────────┘
```

### Components

- **API Process**: FastAPI app serving HTTP/SSE endpoints
- **Worker Process**: arq-based background task processor
- **Listener Process**: WebSocket manager for Kick prediction streams
- **PostgreSQL**: Primary data store with migrations via Alembic
- **Redis**: Pub/sub for SSE, job queue, rate limiting, distributed locks

## Development

### Commands

```bash
make up          # Start all services
make down        # Stop all services
make shell       # Interactive shell in API container
make test        # Run test suite
make lint        # Run linting (ruff + mypy)
make format      # Format code
make migrate     # Run database migrations
make clean       # Clean containers and volumes
```

### Project Structure

```
app/
├── main.py              # FastAPI app factory
├── core/                # Configuration, logging, security
├── db/                  # SQLAlchemy models and database
├── api/v1/             # API route handlers
├── services/           # Business logic layer
├── integrations/       # External service adapters
│   ├── kick/           # Kick.com gateway + OAuth
│   └── cloudinary/     # Optional proof-image hosting
└── workers/            # Background tasks
```

### Testing

Tests use PostgreSQL and Redis instances via Docker Compose. The `FakeKickGateway`
provides deterministic prediction scenarios for integration testing.

The suite deletes users, channels and votes between tests, so it must run against
a dedicated database. On the first run it derives `<DATABASE_URL>_test`, creates
it if missing, and applies migrations automatically — the development database is
never touched. Override the target with `TEST_DATABASE_URL` when needed; the suite
refuses to start if the resolved database does not end in `_test` (or equal the
dev database) unless `ALLOW_DEV_TEST_DB=1` is set.

```bash
# Run all tests against the isolated test database
make test

# Run with coverage
docker compose exec api pytest --cov=app --cov-report=html

# Run specific test file
docker compose exec api pytest tests/test_auth.py -v
```

### Adding Migrations

```bash
# Auto-generate migration from model changes
docker compose exec api alembic revision --autogenerate -m "Description"

# Apply migrations
make migrate

# Rollback one migration
docker compose exec api alembic downgrade -1
```

## Deployment

### Docker Production

```bash
docker build --target production -t kick-backend:latest .
```

Set all production secrets and URLs before starting. Production startup rejects
development credentials, invalid encryption keys, localhost cookie domains, and
non-HTTPS API/CORS origins. Run the API, worker (`arq app.workers.settings.WorkerSettings`),
and listener (`python -m app.workers.listener`) as separate processes. To aggregate
their Prometheus metrics, set `PROMETHEUS_MULTIPROC_DIR` to the same writable shared
volume for all three processes.

### QR / bank-transfer payments

Premium is granted manually after an out-of-band transfer, with no hosted
checkout or external provider:

1. The user opens `/billing`, scans the configured QR, sends the $1 and uploads
   a transfer screenshot (`POST /api/v1/billing/qr-payments`). The submission is
   stored as `pending`; proofs go to Cloudinary when configured, otherwise to
   `QR_PAYMENT_DIR`.
2. An admin opens `/payments`, enters `PAYMENTS_ADMIN_PASSWORD`, and reviews the
   proofs (`GET /api/v1/qr-payments`, images via `GET /api/v1/qr-payments/{id}/proof`).
3. Confirming grants one month of Premium directly on the user row
   (`POST /api/v1/qr-payments/{id}/confirm`); rejecting closes the submission
   without granting anything (`POST /api/v1/qr-payments/{id}/reject`). The user's
   open billing page updates over SSE (`billing_updated`).

Entitlements are granted only by the admin confirm action, never by the browser.


## Operations

### Kill Switch

Instantly pause all voting across the platform:

```bash
curl -X POST http://localhost:8000/api/v1/admin/kill-switch \
  -H "Content-Type: application/json" \
  -H "Origin: http://localhost:3000" \
  -H "Cookie: kick_session=<admin-session-cookie>" \
  -d '{"enabled": true}'
```

The kill switch is persisted in PostgreSQL and mirrored in Redis. Turning it off
does not clear the independent Kick circuit breaker.

### Points-growth analytics

A worker task runs every 15 minutes and records each user's channel-points
balance; the API aggregates the history:

- `GET /api/v1/points/history?range=24h&channel_id=<optional>` returns per-channel
  and combined growth series plus window totals (`current`, `start`, `growth`,
  `growth_pct`), cached per user for 60 seconds. `range` is one of `1h`, `24h`,
  `7d` or `30d`; sub-day windows are bucketed by the hour (`1h` by 5 minutes) and
  day windows by day.
- Balances are read live from Kick and require the user's kick.com site session
  token; users without one are omitted from snapshots (their growth is empty).
- Reading points is the only points operation Kick exposes publicly — there is no
  endpoint to add or set points. The dashboard therefore reports earned growth; it
  never writes balances.

### Monitoring

- **Health**: `GET /healthz` (liveness)
- **Readiness**: `GET /readyz` (includes dependency checks)
- **Metrics**: `GET /metrics` (admin-authenticated Prometheus format; also available
  at `GET /api/v1/metrics`)
- **Admin**: `GET /api/v1/admin/overview` (system status, including ready-queue
  depth and oldest-job lag)

Compose shares a `prometheus_metrics` volume between the API, worker, and listener
so Kick-call, vote-outcome, listener, queue, and breaker metrics are aggregated
across processes. Graceful shutdown removes live-gauge samples for stopped processes.

### Logs

Structured JSON logs in production include:

- Request/response logging with correlation IDs
- Business event logging (votes, settlements, billing)
- Error tracking with context
- Sensitive data automatically redacted

### Token Encryption Key Rotation

1. Generate new Fernet key: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
2. Prepend to `TOKEN_ENCRYPTION_KEYS` (comma-separated)
3. Deploy new configuration
4. After all tokens are re-encrypted, remove old keys

### Canary failures

Check the worker logs and `/api/v1/admin/overview`, then verify
`CANARY_CHANNEL_SLUG` and `CANARY_ACCESS_TOKEN` are configured for a valid
read-only Kick account. Five consecutive failures open the voting circuit breaker
for five minutes; a successful canary clears it. The breaker is independent of
the admin kill switch, so only disable a manually enabled kill switch after the
Kick issue is resolved. Canary failures are logged and surfaced in the app; no
external paging/notification target is configured.

## Integration with Legacy Code

The system wraps the existing prediction logic from `legacy/` via adapter interfaces:

- **KickGateway Protocol**: Abstracts all Kick.com API interactions
- **VoteStrategy**: Encapsulates the smart prediction selection logic
- **Minimal changes**: Only async-safety and importability modifications to legacy code

See `app/integrations/kick/legacy_adapter.py` for the integration layer.

## License

MIT License - see LICENSE file for details.

## Current implementation limits

- Run `make migrate` after every model/migration update before starting processes.
- The committed test suite currently concentrates on schema, model, and health
  smoke tests. Add and run acceptance tests for OAuth state reuse, concurrent plan
  limits, prediction/vote privacy and pagination, QR payment review flows,
  SSE delivery, and worker idempotency before production.
- The CI lint and strict type-check commands must pass before calling the backend
  complete; both currently report existing issues that need to be resolved.
- The listener's Pusher topic authorization requirements and the legacy vote
  adapter's exact error classification still need an end-to-end verification.
- The backend cannot prove point balances using the current legacy adapter.
- Points-growth history is sampled every 15 minutes (and only for accounts with a
  site session token), so it reflects readings over time rather than every balance
  change Kick records internally.
- Premium price is display-only (`premium_price_display`); confirm the QR target,
  `PAYMENTS_ADMIN_PASSWORD`, and proof storage (Cloudinary or `QR_PAYMENT_DIR`)
  before going live.
- Run the complete endpoint/provider test suite and production configuration
  review before treating a deployment as production-ready.
