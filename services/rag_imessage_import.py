# services/rag_imessage_import.py
import json, os
from services.rag_service import add_to_rag

DATA_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "chat_history.json")

def import_imessage_history():
    """
    Embed all iMessage messages from chat_history.json into RAG.
    This gives Argo memory of your real chat history.
    """
    with open(DATA_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    total = 0
    for contact in data:
        contact_id = contact["contact_id"]
        display_name = contact.get("display_name", contact_id)
        for msg in contact["messages"]:
            role = "user" if msg.get("is_from_me") else "contact"
            text = msg.get("text", "")
            if not text:
                continue
            add_to_rag(contact_id, text, role)
            total += 1
    print(f"✅ Imported {total} iMessage messages into RAG.")
