from typing import Dict, List
from models import Contact
from services.openai_bridge import chat_json, embed_texts
from services.rag_store import query_by_contact

ANALYZE_SYSTEM = "You analyze conversations, infer tone, extract durable facts, and summarize. Output JSON only."

def _format_recent(contact: Contact, max_messages: int) -> str:
    msgs = contact.messages[-max_messages:]
    lines = []
    for m in msgs:
        cleaned = m.text.replace("\n", " ").strip()
        lines.append(f"{m.role.upper()}: {cleaned}")
    return "\n".join(lines)


def analyze_contact(contact: Contact, max_messages: int = 80) -> Dict:
    # 1) Recent window
    recent = _format_recent(contact, max_messages)

    # 2) RAG: use last incoming/outgoing messages as queries (take last 3 texts)
    queries = [m.text for m in contact.messages[-3:]]
    if queries:
        q_emb = embed_texts(["\n".join(queries)])  # 1 vector
        hits = query_by_contact(contact.contact_id, q_emb[0], top_k=12)
        rag_context = "\n".join([h["document"] for h in hits])
    else:
        rag_context = ""

    prompt = f"""
Return a JSON object:
{{
  "tone_summary": "string",
  "facts": ["string", "..."],
  "history_summary": "string"
}}

Guidelines:
- Tone: how the USER typically writes to this person (casual/formal/emoji/length).
- Facts: only durable info (preferences, schedules, contact details, commitments).
- History summary: 3–5 sentences, note any promises with dates if present.

Recent window (newest last):
{recent}

Retrieved context (semantic):
{rag_context}
"""
    return chat_json(prompt, system=ANALYZE_SYSTEM)
