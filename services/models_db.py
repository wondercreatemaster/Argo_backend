# services/models_db.py
from typing import Optional, List
from sqlmodel import SQLModel, Field, Relationship


class Discussion(SQLModel, table=True):
    id: str = Field(primary_key=True)
    title: str
    tags: str  # comma-separated
    # Back-reference to messages
    messages: List["Message"] = Relationship(back_populates="discussion")


class Message(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    discussion_id: str = Field(foreign_key="discussion.id")
    role: str
    text: str
    # Link to parent discussion (must use quoted name only)
    discussion: Optional["Discussion"] = Relationship(back_populates="messages")
