import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

# Load .env for local development. On Render, env vars are injected directly
# so the file won't exist — that's fine.
try:
    from dotenv import load_dotenv
    _env_file = Path(__file__).resolve().parents[3] / ".env"
    if _env_file.is_file():
        load_dotenv(_env_file, override=False)
except ImportError:
    pass

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set. Add it to the project .env file or set it in Render.")

# Normalize URL scheme for SQLAlchemy + psycopg2.
# Neon and some providers emit postgres:// or plain postgresql://.
# SQLAlchemy needs the driver suffix postgresql+psycopg2://.
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = "postgresql://" + DATABASE_URL[len("postgres://"):]
if DATABASE_URL.startswith("postgresql://") and "+psycopg2" not in DATABASE_URL:
    DATABASE_URL = "postgresql+psycopg2://" + DATABASE_URL[len("postgresql://"):]

engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_recycle=300)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
