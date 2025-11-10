# services/rag_imessage_import.py
import json
import os
import logging
import hashlib
from services.rag_service import add_to_rag
from services.rag_store import add_messages, get_existing_message_ids, has_indexed_messages, clear_all_messages
from services.openai_bridge import embed_texts
from services import imessage_service as imsg

logger = logging.getLogger(__name__)

def normalize_msg(contact_id: str, ts: str, direction: str, text: str) -> str:
    """Format message for RAG storage."""
    who = "OUT" if direction == "out" else "IN"
    return f"[{ts}] {who}: {text.strip()}"

def _create_message_id(contact_id: str, msg_timestamp: str, text: str) -> str:
    """Create unique, stable message ID from contact_id, timestamp, and text hash."""
    # Create a stable hash-based ID that won't change on re-import
    # Format: contact_id::hash(contact_id + timestamp + text_prefix)
    text_prefix = text[:50] if text else ""  # Use first 50 chars for uniqueness
    content = f"{contact_id}::{msg_timestamp}::{text_prefix}"
    content_hash = hashlib.md5(content.encode('utf-8')).hexdigest()[:12]  # 12 char hash
    return f"{contact_id}::{msg_timestamp}::{content_hash}"

def import_imessage_history_from_db(batch_size: int = 128, incremental: bool = True):
    """
    Embed iMessage messages from chat.db into RAG.
    If incremental=True, only imports new messages not already in RAG.
    If incremental=False, imports all messages (for full re-import).
    
    This gives Argo memory of your real chat history.
    """
    logger.info("Starting import of iMessage history from chat.db...")
    
    # Get existing message IDs if doing incremental import
    existing_ids = set()
    if incremental:
        logger.info("Checking for existing messages in RAG...")
        existing_ids = get_existing_message_ids()
        logger.info(f"Found {len(existing_ids)} existing messages in RAG")
    
    # Get all contacts (no limit)
    contacts = imsg.list_all_contacts()
    logger.info(f"Found {len(contacts)} contacts")
    
    docs, ids, metas = [], [], []
    total_messages = 0
    new_messages = 0
    skipped_messages = 0
    
    for contact in contacts:
        chat_id = int(contact["contact_id"])
        contact_id = contact["contact_id"]
        display_name = contact.get("display_name", "Unknown")
        
        try:
            convo = imsg.get_conversation(chat_id)
            if not convo or not convo.get("messages"):
                continue
                
            for idx, msg in enumerate(convo["messages"]):
                text = msg.get("text", "").strip()
                if not text:
                    continue
                    
                # Map role: "user" -> "out", "other" -> "in"
                direction = "out" if msg.get("role") == "user" else "in"
                ts = msg.get("timestamp", "")
                
                # Create unique, stable message ID based on content
                msg_id = _create_message_id(contact_id, ts, text)
                
                # Skip if already imported (incremental mode)
                if incremental and msg_id in existing_ids:
                    skipped_messages += 1
                    continue
                
                # Normalize and store
                normalized = normalize_msg(contact_id, ts, direction, text)
                docs.append(normalized)
                ids.append(msg_id)
                metas.append({
                    "contact_id": contact_id,
                    "display_name": display_name,
                    "ts": ts,
                    "direction": direction
                })
                total_messages += 1
                new_messages += 1
        except Exception as e:
            logger.warning(f"Error processing contact {contact_id}: {e}")
            continue
    
    if incremental:
        logger.info(f"Found {new_messages} new messages to import (skipped {skipped_messages} existing)")
    else:
        logger.info(f"Processing {total_messages} messages for full import")
    
    if new_messages == 0:
        logger.info("No new messages to import. RAG is up to date.")
        return
    
    # Batch embed and add to RAG
    logger.info(f"Embedding {new_messages} messages in batches of {batch_size}...")
    added_count = 0
    for i in range(0, len(docs), batch_size):
        chunk_docs = docs[i:i+batch_size]
        chunk_ids = ids[i:i+batch_size]
        chunk_metas = metas[i:i+batch_size]
        
        try:
            embs = embed_texts(chunk_docs)
            add_messages(chunk_docs, chunk_ids, chunk_metas, embs)
            added_count += len(chunk_docs)
            if added_count % (batch_size * 5) == 0:
                logger.info(f"Progress: {added_count}/{new_messages} messages indexed...")
        except Exception as e:
            logger.error(f"Error processing batch {i//batch_size}: {e}")
            continue
    
    logger.info(f"Imported {added_count} iMessage messages into RAG from {len(contacts)} contacts.")

def import_new_messages():
    """
    Incrementally import only new messages from chat.db that aren't already in RAG.
    """
    import_imessage_history_from_db(incremental=True)

def clear_and_reimport():
    """
    Clear all indexed messages from RAG and re-import everything from chat.db.
    """
    logger.info("Clearing all indexed messages from RAG...")
    try:
        deleted_count = clear_all_messages()
        logger.info(f"Cleared {deleted_count} messages from RAG")
    except Exception as e:
        logger.error(f"Error clearing RAG: {e}")
        raise
    
    logger.info("Starting full re-import...")
    import_imessage_history_from_db(incremental=False)

def import_imessage_history():
    """
    Legacy function for JSON import. Now redirects to database import.
    """
    import_imessage_history_from_db(incremental=False)
