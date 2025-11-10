# services/unread_service.py
import os
import json
import logging
from typing import List, Dict, Optional
from sqlmodel import select
from services import imessage_service as imsg
from services.models_db import UnreadState
from db import get_session

logger = logging.getLogger(__name__)

# Legacy JSON file path (for migration)
_legacy_json_file = os.path.join(os.path.dirname(__file__), "..", "data", "unread_state.json")

def _migrate_from_json_if_exists():
    """
    Migrate unread state from JSON file to database if JSON file exists.
    This is a one-time migration when switching from JSON to database storage.
    """
    if not os.path.exists(_legacy_json_file):
        return False
    
    try:
        # Load from JSON
        with open(_legacy_json_file, 'r') as f:
            json_state = json.load(f)
        
        if not json_state:
            return False
        
        # Check if database already has data
        with get_session() as session:
            existing_count = len(session.exec(select(UnreadState)).all())
            if existing_count > 0:
                logger.info("Database already has unread state, skipping JSON migration")
                return False
        
        # Migrate to database
        _save_unread_state(json_state)
        logger.info(f"Migrated unread state from JSON to database for {len(json_state)} contacts")
        
        # Optionally backup/rename the old file
        backup_file = _legacy_json_file + ".backup"
        if not os.path.exists(backup_file):
            os.rename(_legacy_json_file, backup_file)
            logger.info(f"Backed up old JSON file to {backup_file}")
        
        return True
    except Exception as e:
        logger.warning(f"Failed to migrate from JSON file: {e}")
        return False

def _load_unread_state() -> Dict[str, int]:
    """Load the last seen message ID for each contact from database."""
    state = {}
    try:
        with get_session() as session:
            all_states = session.exec(select(UnreadState)).all()
            for unread_state in all_states:
                state[unread_state.contact_id] = unread_state.last_seen_message_id
    except Exception as e:
        logger.error(f"Error loading unread state from database: {e}")
    return state

def _save_unread_state(state: Dict[str, int]):
    """Save the last seen message ID for each contact to database."""
    try:
        with get_session() as session:
            for contact_id, message_id in state.items():
                # Try to get existing record
                existing = session.get(UnreadState, contact_id)
                if existing:
                    existing.last_seen_message_id = message_id
                else:
                    # Create new record
                    new_state = UnreadState(
                        contact_id=contact_id,
                        last_seen_message_id=message_id
                    )
                    session.add(new_state)
            session.commit()
    except Exception as e:
        logger.error(f"Error saving unread state to database: {e}")
        raise

def _initialize_state_with_current_messages():
    """
    Initialize the unread state by setting last seen message ID to the current
    latest message ID for each contact. This ensures only NEW messages after
    initialization are considered unread.
    """
    try:
        conn = imsg.connect()
        cur = conn.cursor()
    except Exception as e:
        logger.error(f"Error initializing unread state: {e}")
        return {}
    
    state = {}
    contacts = imsg.list_contacts(limit=None)
    
    for contact in contacts:
        contact_id = contact["contact_id"]
        
        # Get the latest message ID (including both sent and received) for this contact
        cur.execute("""
            SELECT MAX(m.ROWID)
            FROM message m
            JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
            WHERE cmj.chat_id = ? AND m.text IS NOT NULL
        """, (int(contact_id),))
        
        result = cur.fetchone()
        if result and result[0]:
            # Set last seen to the latest message ID, so all current messages are considered "read"
            state[contact_id] = result[0]
            logger.debug(f"Initialized contact {contact_id} with last seen message ID {result[0]}")
    
    conn.close()
    
    if state:
        _save_unread_state(state)
        logger.info(f"Initialized unread state for {len(state)} contacts in database")
    
    return state

def get_unread_messages() -> List[Dict]:
    """
    Get all unread messages by comparing current messages with last seen state.
    Returns only incoming messages (not sent by user) that are newer than last seen.
    Returns the most recent unread message per contact.
    """
    # Try to migrate from JSON if it exists (one-time migration)
    _migrate_from_json_if_exists()
    
    try:
        conn = imsg.connect()
        cur = conn.cursor()
    except FileNotFoundError:
        logger.warning("Chat database not available")
        return []
    except Exception as e:
        logger.error(f"Error connecting to chat database: {e}")
        return []
    
    unread_state = _load_unread_state()
    
    # If state is empty (first run), initialize it with current message IDs
    # This marks all existing messages as "read" so only new ones are unread
    if not unread_state:
        logger.info("Unread state is empty. Initializing with current messages...")
        unread_state = _initialize_state_with_current_messages()
        # After initialization, there should be no unread messages (all current messages are marked as read)
        conn.close()
        return []
    
    unread_messages = []
    
    # Get all contacts
    contacts = imsg.list_contacts(limit=None)
    
    for contact in contacts:
        contact_id = contact["contact_id"]
        
        # If contact is not in state, initialize it with current latest message ID
        if contact_id not in unread_state:
            cur.execute("""
                SELECT MAX(m.ROWID)
                FROM message m
                JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
                WHERE cmj.chat_id = ? AND m.text IS NOT NULL
            """, (int(contact_id),))
            result = cur.fetchone()
            if result and result[0]:
                # Initialize this contact - mark all current messages as read
                unread_state[contact_id] = result[0]
                # Save just this contact to database
                _save_unread_state({contact_id: result[0]})
                logger.debug(f"Initialized new contact {contact_id} with last seen message ID {result[0]}")
                continue  # Skip - no unread messages for newly initialized contact
        
        last_seen_id = unread_state[contact_id]
        
        # Get the latest unread message for this contact (incoming messages only)
        # Only messages with ROWID greater than last_seen_id are considered unread
        cur.execute("""
            SELECT m.ROWID, m.text, m.date, m.is_from_me, c.display_name, h.id
            FROM message m
            JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
            JOIN chat c ON cmj.chat_id = c.ROWID
            LEFT JOIN handle h ON m.handle_id = h.ROWID
            WHERE cmj.chat_id = ? 
            AND m.text IS NOT NULL 
            AND m.is_from_me = 0
            AND m.ROWID > ?
            ORDER BY m.date DESC
            LIMIT 1
        """, (int(contact_id), last_seen_id))
        
        result = cur.fetchone()
        if result:
            msg_id, text, date, is_from_me, display_name, handle_id = result
            ts = imsg.apple_to_iso(date)
            unread_messages.append({
                "contact_id": contact_id,
                "display_name": display_name or handle_id or "Unknown",
                "message": text or "",
                "timestamp": ts,
                "message_id": msg_id,
            })
    
    conn.close()
    
    # Sort by timestamp (newest first)
    unread_messages.sort(key=lambda x: x["timestamp"], reverse=True)
    
    # Always sync new messages to RAG (both incoming and outgoing)
    # This happens automatically when checking for unread messages
    try:
        sync_new_messages_to_rag()
    except Exception as e:
        logger.warning(f"Failed to sync new messages to RAG: {e}")
    
    return unread_messages

def mark_contact_as_read(contact_id: str, message_id: int):
    """Mark a contact as read by storing the last seen message ID in database."""
    try:
        with get_session() as session:
            existing = session.get(UnreadState, contact_id)
            if existing:
                if message_id >= existing.last_seen_message_id:
                    existing.last_seen_message_id = message_id
                    session.add(existing)
                    session.commit()
                    logger.debug(f"Marked contact {contact_id} as read up to message {message_id}")
            else:
                # Create new record
                new_state = UnreadState(
                    contact_id=contact_id,
                    last_seen_message_id=message_id
                )
                session.add(new_state)
                session.commit()
                logger.debug(f"Created unread state for contact {contact_id} with message ID {message_id}")
    except Exception as e:
        logger.error(f"Error marking contact as read: {e}")
        raise

def reset_unread_state():
    """
    Reset the unread state by clearing the database and re-initializing with current messages.
    This will mark all current messages as read, so only NEW messages after
    this call will be considered unread.
    """
    logger.info("Resetting unread state in database...")
    
    # Clear all existing unread state records
    try:
        with get_session() as session:
            all_states = list(session.exec(select(UnreadState)).all())
            count = len(all_states)
            for state in all_states:
                session.delete(state)
            session.commit()
            logger.info(f"Cleared {count} existing unread state records from database")
    except Exception as e:
        logger.error(f"Error clearing unread state: {e}")
        raise
    
    # Re-initialize with current messages
    state = _initialize_state_with_current_messages()
    return state

def sync_new_messages_to_rag():
    """
    Sync new messages (both incoming and outgoing) to RAG for all contacts.
    Only syncs messages that are newer than the last seen message ID.
    """
    try:
        from services.rag_store import add_messages, get_existing_message_ids
        from services.rag_imessage_import import normalize_msg, _create_message_id
        from services.openai_bridge import embed_texts
        
        conn = imsg.connect()
        cur = conn.cursor()
    except Exception as e:
        logger.error(f"Error connecting to database for RAG sync: {e}")
        return
    
    unread_state = _load_unread_state()
    if not unread_state:
        logger.debug("No unread state found, skipping RAG sync")
        conn.close()
        return
    
    # Get existing message IDs from RAG to avoid duplicates
    existing_ids = get_existing_message_ids()
    logger.debug(f"Found {len(existing_ids)} existing messages in RAG")
    
    contacts = imsg.list_contacts(limit=None)
    all_new_messages = []
    
    for contact in contacts:
        contact_id = contact["contact_id"]
        
        # Skip if contact not in state
        if contact_id not in unread_state:
            continue
        
        last_seen_id = unread_state[contact_id]
        display_name = contact.get("display_name", "Unknown")
        
        # Get ALL new messages (both incoming and outgoing) since last seen
        cur.execute("""
            SELECT m.ROWID, m.text, m.date, m.is_from_me, c.display_name, h.id
            FROM message m
            JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
            JOIN chat c ON cmj.chat_id = c.ROWID
            LEFT JOIN handle h ON m.handle_id = h.ROWID
            WHERE cmj.chat_id = ? 
            AND m.text IS NOT NULL 
            AND m.ROWID > ?
            ORDER BY m.date ASC
        """, (int(contact_id), last_seen_id))
        
        results = cur.fetchall()
        
        for msg_id, text, date, is_from_me, contact_display_name, handle_id in results:
            # Create stable message ID for RAG
            ts = imsg.apple_to_iso(date)
            direction = "out" if is_from_me else "in"
            msg_id_str = _create_message_id(contact_id, ts, text)
            
            # Skip if already in RAG
            if msg_id_str in existing_ids:
                continue
            
            # Normalize and prepare for RAG
            normalized = normalize_msg(contact_id, ts, direction, text)
            all_new_messages.append({
                "doc": normalized,
                "id": msg_id_str,
                "rowid": msg_id,  # Store actual database ROWID
                "metadata": {
                    "contact_id": contact_id,
                    "display_name": contact_display_name or handle_id or display_name,
                    "ts": ts,
                    "direction": direction
                }
            })
    
    conn.close()
    
    if not all_new_messages:
        logger.debug("No new messages to sync to RAG")
        return
    
    # Batch embed and add to RAG
    batch_size = 128
    logger.info(f"Syncing {len(all_new_messages)} new messages to RAG...")
    
    synced_count = 0
    for i in range(0, len(all_new_messages), batch_size):
        batch = all_new_messages[i:i+batch_size]
        docs = [msg["doc"] for msg in batch]
        ids = [msg["id"] for msg in batch]
        metas = [msg["metadata"] for msg in batch]
        
        try:
            embs = embed_texts(docs)
            add_messages(docs, ids, metas, embs)
            synced_count += len(batch)
            logger.debug(f"Synced {synced_count}/{len(all_new_messages)} messages to RAG")
        except Exception as e:
            logger.error(f"Error syncing batch to RAG: {e}")
            continue
    
    if synced_count > 0:
        logger.info(f"Successfully synced {synced_count} new messages to RAG")
        # Note: We don't update last_seen_id here because:
        # - last_seen_id is for tracking which messages the user has "read" (seen)
        # - RAG sync is separate - we want all messages (incoming and outgoing) in RAG
        # - last_seen_id only gets updated when user explicitly marks messages as read
        # - The duplicate check (existing_ids) prevents re-syncing the same messages

