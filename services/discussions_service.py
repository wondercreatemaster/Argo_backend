# services/discussions_service.py
from sqlmodel import select
from services.models_db import Discussion, Message
from services.rag_service import add_to_rag, query_rag
from services.openai_bridge import chat_complete
from db import get_session
import uuid

def list_discussions():
    with get_session() as session:
        discussions = session.exec(select(Discussion)).all()
        return [
            {"id": d.id, "title": d.title, "tags": d.tags.split(",") if d.tags else []}
            for d in discussions
        ]

def get_discussion(discussion_id: str):
    with get_session() as session:
        discussion = session.get(Discussion, discussion_id)
        if not discussion:
            return {"title": "", "tags": [], "messages": []}

        msgs = session.exec(select(Message).where(Message.discussion_id == discussion_id)).all()
        return {
            "title": discussion.title,
            "tags": discussion.tags.split(",") if discussion.tags else [],
            "messages": [{"role": m.role, "text": m.text} for m in msgs],
        }

def start_discussion(title: str, tags: list[str]):
    new_id = str(uuid.uuid4())
    tags_str = ",".join(tags)
    with get_session() as session:
        d = Discussion(id=new_id, title=title, tags=tags_str)
        session.add(d)
        session.commit()
    return new_id

def delete_discussion(discussion_id: str):
    from services.rag_service import delete_from_rag  # optional cleanup if you implemented it

    with get_session() as session:
        discussion = session.get(Discussion, discussion_id)
        if not discussion:
            raise ValueError("Discussion not found")

        # delete all related messages first
        msgs = session.exec(select(Message).where(Message.discussion_id == discussion_id)).all()
        for m in msgs:
            session.delete(m)

        # delete the discussion
        session.delete(discussion)
        session.commit()

    # optional: remove from vector DB memory
    try:
        delete_from_rag(discussion_id)
    except Exception as e:
        print(f"⚠️ RAG cleanup failed: {e}")

    return {"status": "ok", "id": discussion_id}

def chat_in_discussion(discussion_id: str, user_message: str):
    with get_session() as session:
        discussion = session.get(Discussion, discussion_id)
        if not discussion:
            raise ValueError("Discussion not found")

        # Save user message
        m_user = Message(discussion_id=discussion_id, role="user", text=user_message)
        session.add(m_user)
        session.commit()
        add_to_rag(discussion_id, user_message, "user")

        # Retrieve context
        retrieved = query_rag(user_message, top_k=5)
        context = "\n".join(retrieved.get("documents", [[]])[0]) if retrieved.get("documents") else ""

        # Generate AI reply
        system_prompt = (
            "You are Argo, a helpful assistant that maintains long-term context across discussions."
            "Refer to prior context where relevant."
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Context:\n{context}\n\nUser: {user_message}"},
        ]
        reply = chat_complete(messages)

        # Save assistant message
        m_assistant = Message(discussion_id=discussion_id, role="assistant", text=reply)
        session.add(m_assistant)
        session.commit()
        add_to_rag(discussion_id, reply, "assistant")
        return reply
