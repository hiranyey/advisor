"""SQLAlchemy engine, session factory, and the declarative Base."""

from collections.abc import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings

engine = create_engine(settings.database_url, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def ensure_database() -> None:
    """Create the target database if it doesn't exist.

    `create_all` only makes tables inside an existing database; a fresh host (or a
    `dropdb`) leaves nothing to connect to. Connect to the `postgres` maintenance DB
    and CREATE DATABASE first so `seed.py` is truly one-command from zero.
    """
    url = make_url(settings.database_url)
    dbname = url.database
    admin_engine = create_engine(
        url.set(database="postgres"), isolation_level="AUTOCOMMIT"
    )
    try:
        with admin_engine.connect() as conn:
            exists = conn.execute(
                text("select 1 from pg_database where datname = :n"), {"n": dbname}
            ).scalar()
            if not exists:
                conn.execute(text(f'create database "{dbname}"'))  # ident can't be bound
                print(f"created database: {dbname}")
            else:
                print(f"database exists: {dbname}")
    finally:
        admin_engine.dispose()


class Base(DeclarativeBase):
    pass


def get_session() -> Iterator[Session]:
    """FastAPI dependency: yields a session, closes it after the request."""
    with SessionLocal() as session:
        yield session
