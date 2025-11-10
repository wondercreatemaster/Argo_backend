from pydantic import BaseModel, Field
from typing import List, Optional

class Message(BaseModel):
    timestamp: str
    role: str  # "in" or "out"
    text: str
    sender: str

class Contact(BaseModel):
    contact_id: str
    display_name: str
    messages: List[Message] = Field(default_factory=list)

class ContactListItem(BaseModel):
    contact_id: str
    display_name: str
    last_message_ts: Optional[str] = None
    last_message_snippet: Optional[str] = None
    total_messages: int

class AnalysisRequest(BaseModel):
    max_messages: int = 80

class AnalysisResult(BaseModel):
    contact_id: str
    display_name: str
    tone_summary: str
    facts: List[str]
    history_summary: str

class UnreadMessage(BaseModel):
    contact_id: str
    display_name: str
    message: str
    timestamp: str
    message_id: int

class MarkReadRequest(BaseModel):
    contact_id: str
    message_id: int
