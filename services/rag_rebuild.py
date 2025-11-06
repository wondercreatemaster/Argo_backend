# services/rag_rebuild.py
from services.rag_service import add_to_rag
from services.models_db import Discussion, Message
from db import get_session

def rebuild_rag_from_db():
    """
    Re-indexes all discussion messages into the RAG vector store.
    Run this once if the RAG database is empty or after migration.
    """
    with get_session() as session:
        discussions = session.query(Discussion).all()
        total_added = 0

        for d in discussions:
            print(f"🔁 Rebuilding RAG for discussion '{d.title}' ({d.id})")
            msgs = session.query(Message).filter(Message.discussion_id == d.id).all()
            for m in msgs:
                add_to_rag(d.id, m.text, m.role)
                total_added += 1

        print(f"✅ RAG rebuild complete. Indexed {total_added} messages.")
