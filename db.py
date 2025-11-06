# db.py
from sqlmodel import SQLModel, create_engine, Session
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "argo.db")
engine = create_engine(f"sqlite:///{DB_PATH}", echo=False)

def init_db():
    from services.models_db import Discussion, Message  # import models here
    SQLModel.metadata.create_all(engine)

def get_session():
    return Session(engine)
