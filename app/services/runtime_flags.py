"""Persistent global runtime flags."""

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import SystemSetting

KILL_SWITCH_KEY = "admin:kill_switch"


async def is_kill_switch_enabled(db: AsyncSession) -> bool:
    """Read the persisted operator-controlled voting kill switch."""
    setting = await db.get(SystemSetting, KILL_SWITCH_KEY)
    return setting is not None and setting.value == "1"


async def set_kill_switch_enabled(db: AsyncSession, enabled: bool) -> None:
    """Persist the operator-controlled voting kill switch."""
    insert_statement = (
        sqlite_insert(SystemSetting)
        if db.get_bind().dialect.name == "sqlite"
        else pg_insert(SystemSetting)
    )
    await db.execute(
        insert_statement.values(
            key=KILL_SWITCH_KEY,
            value="1" if enabled else "0",
        ).on_conflict_do_update(
            index_elements=[SystemSetting.key],
            set_={"value": "1" if enabled else "0"},
        )
    )
