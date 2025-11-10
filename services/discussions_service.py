# services/discussions_service.py
import logging
from sqlmodel import select
from services.models_db import Discussion, Message
from services.rag_service import add_to_rag, query_rag
from services.rag_store import query_chat_history
from services.openai_bridge import chat_complete, chat_complete_stream, embed_texts
from db import get_session
import uuid

logger = logging.getLogger(__name__)

# Context size limits to prevent oversized prompts
MAX_CONTEXT_LENGTH = 8000  # Maximum characters for context
MAX_DISCUSSION_CONTEXT_LENGTH = 3000  # Max for discussion context
MAX_CHAT_HISTORY_CONTEXT_LENGTH = 5000  # Max for chat history context

def _truncate_context(text: str, max_length: int) -> str:
    """Truncate context text if it exceeds max_length, preserving the end."""
    if len(text) <= max_length:
        return text
    # Keep the end of the context (most recent messages)
    return "..." + text[-(max_length - 3):]

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
        logger.warning(f"RAG cleanup failed: {e}")

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

        # Retrieve context from discussion messages
        discussion_retrieved = query_rag(user_message, top_k=5)
        discussion_context = ""
        if discussion_retrieved.get("documents") and len(discussion_retrieved["documents"]) > 0:
            discussion_context = "\n".join(discussion_retrieved["documents"][0])

        # Retrieve relevant chat history from chat.db
        chat_history_context = ""
        try:
            query_embedding = embed_texts([user_message])[0]
            chat_history_hits = query_chat_history(query_embedding, top_k=8)
            if chat_history_hits:
                # Format chat history messages with metadata
                history_lines = []
                for hit in chat_history_hits:
                    doc = hit.get("document", "")
                    meta = hit.get("metadata", {})
                    display_name = meta.get("display_name", "Contact")
                    # Extract direction info from document format: [timestamp] OUT/IN: text
                    history_lines.append(f"{doc}")
                chat_history_context = "\n".join(history_lines)
        except Exception as e:
            logger.warning(f"Error retrieving chat history: {e}")
            chat_history_context = ""

        # Combine contexts with size limits
        context_parts = []
        if discussion_context:
            truncated_discussion = _truncate_context(discussion_context, MAX_DISCUSSION_CONTEXT_LENGTH)
            if len(discussion_context) > MAX_DISCUSSION_CONTEXT_LENGTH:
                logger.debug(f"Truncated discussion context from {len(discussion_context)} to {len(truncated_discussion)} chars")
            context_parts.append(f"=== Discussion Context ===\n{truncated_discussion}")
        if chat_history_context:
            truncated_history = _truncate_context(chat_history_context, MAX_CHAT_HISTORY_CONTEXT_LENGTH)
            if len(chat_history_context) > MAX_CHAT_HISTORY_CONTEXT_LENGTH:
                logger.debug(f"Truncated chat history context from {len(chat_history_context)} to {len(truncated_history)} chars")
            context_parts.append(f"=== Chat History Context (from iMessage) ===\n{truncated_history}")
        
        combined_context = "\n\n".join(context_parts) if context_parts else ""
        
        # Final safety check - truncate entire context if still too long
        if len(combined_context) > MAX_CONTEXT_LENGTH:
            logger.warning(f"Combined context exceeded {MAX_CONTEXT_LENGTH} chars, truncating")
            combined_context = _truncate_context(combined_context, MAX_CONTEXT_LENGTH)

        # Generate AI reply
        system_prompt = (
            "You are Argo, a helpful assistant that maintains long-term context across discussions and chat history. "
            "You have access to both the current discussion context and the user's iMessage chat history. "
            "Use both sources to provide informed, contextually relevant responses. "
            "When referencing chat history, you can mention relevant conversations or information from past messages."
        )
        
        user_content = f"{combined_context}\n\nUser: {user_message}" if combined_context else user_message
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]
        reply = chat_complete(messages)

        # Save assistant message
        m_assistant = Message(discussion_id=discussion_id, role="assistant", text=reply)
        session.add(m_assistant)
        session.commit()
        add_to_rag(discussion_id, reply, "assistant")
        return reply

def chat_in_discussion_stream(discussion_id: str, user_message: str):
    """
    Stream chat response for a discussion, yielding chunks as they're generated.
    Also saves the complete message to database after streaming completes.
    Yields: (chunk: str, full_text: str) tuples.
    """
    with get_session() as session:
        discussion = session.get(Discussion, discussion_id)
        if not discussion:
            raise ValueError("Discussion not found")

        # Save user message
        m_user = Message(discussion_id=discussion_id, role="user", text=user_message)
        session.add(m_user)
        session.commit()
        add_to_rag(discussion_id, user_message, "user")

        # Retrieve context from discussion messages
        discussion_retrieved = query_rag(user_message, top_k=5)
        discussion_context = ""
        if discussion_retrieved.get("documents") and len(discussion_retrieved["documents"]) > 0:
            discussion_context = "\n".join(discussion_retrieved["documents"][0])

        # Retrieve relevant chat history from chat.db
        chat_history_context = ""
        try:
            query_embedding = embed_texts([user_message])[0]
            chat_history_hits = query_chat_history(query_embedding, top_k=8)
            if chat_history_hits:
                # Format chat history messages with metadata
                history_lines = []
                for hit in chat_history_hits:
                    doc = hit.get("document", "")
                    meta = hit.get("metadata", {})
                    display_name = meta.get("display_name", "Contact")
                    # Extract direction info from document format: [timestamp] OUT/IN: text
                    history_lines.append(f"{doc}")
                chat_history_context = "\n".join(history_lines)
        except Exception as e:
            logger.warning(f"Error retrieving chat history: {e}")
            chat_history_context = ""

        # Combine contexts with size limits
        context_parts = []
        if discussion_context:
            truncated_discussion = _truncate_context(discussion_context, MAX_DISCUSSION_CONTEXT_LENGTH)
            if len(discussion_context) > MAX_DISCUSSION_CONTEXT_LENGTH:
                logger.debug(f"Truncated discussion context from {len(discussion_context)} to {len(truncated_discussion)} chars")
            context_parts.append(f"=== Discussion Context ===\n{truncated_discussion}")
        if chat_history_context:
            truncated_history = _truncate_context(chat_history_context, MAX_CHAT_HISTORY_CONTEXT_LENGTH)
            if len(chat_history_context) > MAX_CHAT_HISTORY_CONTEXT_LENGTH:
                logger.debug(f"Truncated chat history context from {len(chat_history_context)} to {len(truncated_history)} chars")
            context_parts.append(f"=== Chat History Context (from iMessage) ===\n{truncated_history}")
        
        combined_context = "\n\n".join(context_parts) if context_parts else ""
        
        # Final safety check - truncate entire context if still too long
        if len(combined_context) > MAX_CONTEXT_LENGTH:
            logger.warning(f"Combined context exceeded {MAX_CONTEXT_LENGTH} chars, truncating")
            combined_context = _truncate_context(combined_context, MAX_CONTEXT_LENGTH)

        # Generate AI reply with streaming
        system_prompt = (
            "You are Argo, a helpful assistant that maintains long-term context across discussions and chat history. "
            "You have access to both the current discussion context and the user's iMessage chat history. "
            "Use both sources to provide informed, contextually relevant responses. "
            "When referencing chat history, you can mention relevant conversations or information from past messages."
        )
        
        user_content = f"{combined_context}\n\nUser: {user_message}" if combined_context else user_message
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]
        
        # Stream the response
        full_reply = ""
        try:
            for chunk, full_text in chat_complete_stream(messages):
                full_reply = full_text
                yield chunk, full_text
        finally:
            # Save assistant message after streaming completes (or on error)
            if full_reply:
                try:
                    m_assistant = Message(discussion_id=discussion_id, role="assistant", text=full_reply)
                    session.add(m_assistant)
                    session.commit()
                    add_to_rag(discussion_id, full_reply, "assistant")
                except Exception as e:
                    logger.error(f"Error saving assistant message: {e}")
