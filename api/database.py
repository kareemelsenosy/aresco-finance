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
    _seed_reference_data()


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
