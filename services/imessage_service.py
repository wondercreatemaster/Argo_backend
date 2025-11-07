import os
import sqlite3
from datetime import datetime
from typing import List, Dict, Any

DB_PATH = os.path.expanduser("~/Desktop/chat.db")

def connect():
    return sqlite3.connect(DB_PATH)

def apple_to_iso(apple_time):
    """Convert Apple's timestamp to ISO UTC string."""
    if not apple_time:
        return None
    # Detect nanoseconds-based timestamp
    if apple_time > 10**12:
        apple_time /= 1_000_000_000
    unix_time = apple_time + 978307200  # convert from 2001 epoch to 1970 epoch
    return datetime.utcfromtimestamp(unix_time).strftime("%Y-%m-%dT%H:%M:%SZ")

def list_contacts(limit: int = 100) -> List[Dict]:
    """
    Returns list of chat threads with their last message snippet.
    """
    conn = connect()
    cur = conn.cursor()

    if limit:
        query = """
        SELECT
            c.ROWID as chat_id,
            c.display_name,
            h.id as handle_id,
            m.text,
            m.date
        FROM chat c
        LEFT JOIN chat_message_join cmj ON cmj.chat_id = c.ROWID
        LEFT JOIN message m ON cmj.message_id = m.ROWID
        LEFT JOIN handle h ON m.handle_id = h.ROWID
        WHERE m.text IS NOT NULL
        GROUP BY c.ROWID
        ORDER BY m.date DESC
        LIMIT ?
        """
        cur.execute(query, (limit,))
    else:
        query = """
        SELECT
            c.ROWID as chat_id,
            c.display_name,
            h.id as handle_id,
            m.text,
            m.date
        FROM chat c
        LEFT JOIN chat_message_join cmj ON cmj.chat_id = c.ROWID
        LEFT JOIN message m ON cmj.message_id = m.ROWID
        LEFT JOIN handle h ON m.handle_id = h.ROWID
        WHERE m.text IS NOT NULL
        GROUP BY c.ROWID
        ORDER BY m.date DESC
        """
        cur.execute(query)
    rows = cur.fetchall()
    conn.close()

    results = []
    for r in rows:
        last_ts = apple_to_iso(r[4])
        results.append({
            "contact_id": str(r[0]),
            "display_name": r[1] or r[2] or "Unknown",
            "last_message_ts": last_ts,
            "last_message_snippet": (r[3] or "")[:80],
            "total_messages": None,
        })
    return results

def list_all_contacts() -> List[Dict]:
    """
    Returns all chat threads (no limit) for RAG import.
    """
    return list_contacts(limit=None)

def get_conversation(chat_id: int) -> Dict[str, Any]:
    """Return all messages and metadata for one chat thread."""
    conn = connect()
    cur = conn.cursor()

    # Get display name (chat name or handle)
    cur.execute("""
        SELECT c.display_name, h.id
        FROM chat c
        LEFT JOIN chat_handle_join chj ON chj.chat_id = c.ROWID
        LEFT JOIN handle h ON chj.handle_id = h.ROWID
        WHERE c.ROWID = ?
        LIMIT 1
    """, (chat_id,))
    meta = cur.fetchone()
    display_name = meta[0] or meta[1] or "Unknown" if meta else "Unknown"

    # Get messages
    cur.execute("""
        SELECT
            m.is_from_me,
            m.text,
            m.date,
            h.id
        FROM chat_message_join cmj
        JOIN message m ON cmj.message_id = m.ROWID
        LEFT JOIN handle h ON m.handle_id = h.ROWID
        WHERE cmj.chat_id = ?
        ORDER BY m.date ASC
    """, (chat_id,))
    rows = cur.fetchall()
    conn.close()

    messages = []
    for is_from_me, text, date, sender in rows:
        ts = apple_to_iso(date)
        messages.append({
            "role": "user" if is_from_me else "other",
            "text": text or "",
            "timestamp": ts,
            "sender": sender or display_name,
        })

    # ✅ match exactly your desired format
    return {
        "contact_id": str(chat_id),
        "display_name": display_name,
        "messages": messages
    }