"""Database base configuration."""

import re
from typing import Any

from sqlalchemy import MetaData, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import declarative_base, declared_attr

# Create naming convention for constraints to avoid conflicts
convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(naming_convention=convention)


class Base:
    """Base class for all SQLAlchemy models."""

    @declared_attr
    def __tablename__(cls) -> str:
        """Generate table name from class name."""
        # Convert CamelCase to snake_case
        name = re.sub("(.)([A-Z][a-z]+)", r"\1_\2", cls.__name__)
        return re.sub("([a-z0-9])([A-Z])", r"\1_\2", name).lower()

    def dict(self) -> dict[str, Any]:
        """Convert model to dictionary."""
        return {
            column.name: getattr(self, column.name) for column in self.__table__.columns
        }


# Create declarative base
BaseModel = declarative_base(metadata=metadata, cls=Base)


@event.listens_for(Engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    """Set SQLite pragmas for better performance (only applies to SQLite)."""
    # Only apply PRAGMA to SQLite connections
    if (
        hasattr(dbapi_connection, "execute")
        and "sqlite" in str(type(dbapi_connection)).lower()
    ):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")
