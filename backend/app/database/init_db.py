from app.database.db import Base, engine
import app.models  # Ensures all models are registered on Base


def init_db():
    Base.metadata.create_all(bind=engine)
