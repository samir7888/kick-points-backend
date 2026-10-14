"""Wire DB writes to Redis cache invalidation via LISTEN/NOTIFY.

Every tracked table (vote, channel, prediction, user_settings, kick_token,
"user") emits a NOTIFY with the owning user id (or channel slug for
predictions). The API consumes it and invalidates that user's Redis cache, so
caches reflect DB changes from any source — API, worker, listener, or direct
SQL edits.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20261012_0300"
down_revision: str | None = "20261012_0200"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FUNCTION = """
CREATE OR REPLACE FUNCTION kick_cache_invalidate()
RETURNS TRIGGER AS $$
DECLARE
    payload TEXT;
BEGIN
    payload := json_build_object(
        'table', TG_TABLE_NAME,
        'user_id', COALESCE(row_to_json(NEW) ->> 'user_id',
                            row_to_json(OLD) ->> 'user_id'),
        'channel_slug',
            CASE WHEN TG_TABLE_NAME = 'prediction'
                 THEN COALESCE(row_to_json(NEW) ->> 'channel_slug',
                               row_to_json(OLD) ->> 'channel_slug')
                 ELSE NULL END
    )::text;
    PERFORM pg_notify('db_cache_invalidate', payload);
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;
"""

_TABLES = ("vote", "channel", "prediction", "user_settings", "kick_token", '"user"')


def upgrade() -> None:
    op.execute(_FUNCTION)
    for table in _TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS trg_cache_invalidate ON {table};")
        op.execute(
            f"CREATE TRIGGER trg_cache_invalidate AFTER INSERT OR UPDATE OR DELETE "
            f"ON {table} FOR EACH ROW EXECUTE FUNCTION kick_cache_invalidate();"
        )


def downgrade() -> None:
    for table in _TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS trg_cache_invalidate ON {table};")
    op.execute("DROP FUNCTION IF EXISTS kick_cache_invalidate();")