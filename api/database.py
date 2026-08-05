from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from api.config import settings

engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False} if "sqlite" in settings.database_url else {},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    from api import models  # noqa: F401
    Base.metadata.create_all(bind=engine)
    _migrate()
    _seed_reference_data()


def _migrate():
    """Additive column migrations for databases created before a column existed.

    create_all() builds missing tables but never alters existing ones. Each
    entry here is idempotent.
    """
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    wanted = {"receivables": [("ar_class", "VARCHAR")]}
    with engine.begin() as conn:
        for table, columns in wanted.items():
            if not inspector.has_table(table):
                continue
            existing = {c["name"] for c in inspector.get_columns(table)}
            for name, sql_type in columns:
                if name not in existing:
                    conn.execute(text(f'ALTER TABLE "{table}" ADD COLUMN {name} {sql_type}'))


def _seed_reference_data():
    """Seed banks, currencies, and the default check-status map on first run."""
    from api import models

    db = SessionLocal()
    try:
        if db.query(models.Bank).count() == 0:
            for name, aliases in models.DEFAULT_BANKS:
                db.add(models.Bank(name=name, aliases=aliases))
        if db.query(models.CheckStatusMap).count() == 0:
            for raw, delivered, label in models.DEFAULT_CHECK_STATUSES:
                db.add(
                    models.CheckStatusMap(
                        raw_status=raw, delivered=delivered, english_label=label
                    )
                )
        db.commit()
    finally:
        db.close()
