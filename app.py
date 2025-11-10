import os, json
import logging
import asyncio
from typing import List, Dict, Optional
from fastapi import FastAPI, HTTPException, Body, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse
from dotenv import load_dotenv
from models import Contact, ContactListItem, AnalysisResult, AnalysisRequest, Message, UnreadMessage, MarkReadRequest
from services.analysis_service import analyze_contact
from services import discussions_service as ds
from db import init_db
from services import imessage_service as imsg

# Load environment variables first
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Validate required environment variables
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    logger.error("OPENAI_API_KEY environment variable is required but not set")
    raise ValueError("OPENAI_API_KEY environment variable is required. Please set it in your .env file or environment.")

# Initialize database
try:
    init_db()
    logger.info("Database initialized successfully")
except Exception as e:
    logger.error(f"Failed to initialize database: {e}")
    raise

app = FastAPI(title="Argo Backend", version="0.1.0")

# Configure CORS - allow specific origins or localhost for development
ALLOWED_ORIGINS = os.getenv("CORS_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000").split(",")
ALLOWED_ORIGINS = [origin.strip() for origin in ALLOWED_ORIGINS if origin.strip()]

# In development, allow localhost origins
if os.getenv("ENVIRONMENT", "development") == "development":
    ALLOWED_ORIGINS.extend([
        "http://localhost:*",
        "http://127.0.0.1:*",
    ])
    logger.warning("⚠️  CORS is configured for development. Set ENVIRONMENT=production and CORS_ORIGINS for production.")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS if ALLOWED_ORIGINS else ["*"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
)

@app.on_event("startup")
async def startup_event():
    """
    On server startup, check if chat history needs to be imported to RAG.
    Only imports if RAG is empty or new messages are available.
    This allows the chatbot to discuss your chat history.
    """
    try:
        from services.rag_imessage_import import import_new_messages
        from services.rag_store import has_indexed_messages
        
        if has_indexed_messages():
            logger.info("📊 RAG already contains indexed messages. Checking for new messages...")
            import_new_messages()  # Only import new messages
        else:
            logger.info("🔄 RAG is empty. Starting initial import of chat history...")
            from services.rag_imessage_import import import_imessage_history_from_db
            import_imessage_history_from_db(incremental=False)  # Full import on first run
        logger.info("✅ Chat history sync completed.")
    except Exception as e:
        logger.warning(f"⚠️ Warning: Failed to sync chat history on startup: {e}")
        logger.info("   You can manually trigger import via /admin/rebuild_imessage_rag endpoint")
    
    # Start background task for checking unread messages
    asyncio.create_task(check_unread_messages_periodically())
    logger.info("Started background task for checking unread messages (every 5 seconds)")

ANALYSIS_CACHE: Dict[str, AnalysisResult] = {}

def snip(t: str, n=96) -> str:
    t = t.replace("\n"," ").strip()
    return t if len(t)<=n else (t[:n]+"…")

# Health and readiness endpoints
@app.get("/health")
def health_check():
    """Basic health check endpoint."""
    return {"status": "healthy"}

@app.get("/ready")
def readiness_check():
    """Readiness check - verifies database and vector store connectivity."""
    try:
        # Check database connection
        from db import get_session
        from sqlmodel import text
        with get_session() as session:
            session.exec(text("SELECT 1"))
        
        # Check vector store
        from services.rag_store import get_collection
        col = get_collection()
        col.count()
        
        return {"status": "ready", "database": "connected", "vector_store": "connected"}
    except Exception as e:
        logger.error(f"Readiness check failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Service not ready: {str(e)}"
        )

@app.get("/contacts")
def contacts_list():
    return imsg.list_contacts()

@app.get("/contacts/{chat_id}")
def contact_detail(chat_id: int):
    return imsg.get_conversation(chat_id)

@app.post("/contacts/{contact_id}/analyze", response_model=AnalysisResult)
def contact_analyze(contact_id: str, req: AnalysisRequest = Body(default=AnalysisRequest()), force_refresh: bool = False):
    """
    Analyze a contact's conversation history.
    
    Args:
        contact_id: The contact ID to analyze
        req: Analysis request with max_messages parameter
        force_refresh: If True, bypass cache and regenerate analysis
    """
    try:
        convo = imsg.get_conversation(int(contact_id))
    except FileNotFoundError as e:
        logger.error(f"Chat database not found: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Chat database not available. Please ensure chat.db is accessible."
        )
    except Exception as e:
        logger.error(f"Database error for contact {contact_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error: {e}"
        )

    if not convo or not convo.get("messages"):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No messages found for this contact"
        )

    # Check cache unless force_refresh is True
    if not force_refresh and contact_id in ANALYSIS_CACHE:
        logger.debug(f"Returning cached analysis for contact {contact_id}")
        return ANALYSIS_CACHE[contact_id]

    # Convert dict to Contact object
    try:
        messages = [
            Message(
                timestamp=msg["timestamp"],
                role="out" if msg["role"] == "user" else "in",
                text=msg["text"],
                sender=msg["sender"]
            )
            for msg in convo["messages"]
        ]
        c: Contact = Contact(
            contact_id=convo["contact_id"],
            display_name=convo["display_name"],
            messages=messages
        )
        data = analyze_contact(c, req.max_messages)
        result = AnalysisResult(
            contact_id=contact_id,
            display_name=c.display_name,
            tone_summary=data.get("tone_summary",""),
            facts=data.get("facts",[]),
            history_summary=data.get("history_summary","")
        )
        ANALYSIS_CACHE[contact_id] = result
        logger.info(f"Analysis completed for contact {contact_id}")
        return result
    except Exception as e:
        logger.error(f"Analysis failed for contact {contact_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Analysis failed: {e}"
        )

@app.get("/discussions")
def get_all_discussions():
    return ds.list_discussions()

@app.post("/discussions/start")
def start_discussion(req: dict):
    return {"id": ds.start_discussion(req["title"], req.get("tags", []))}

@app.get("/discussions/{discussion_id}")
def get_discussion_route(discussion_id: str):
    detail = ds.get_discussion(discussion_id)
    return detail or {"title": "", "tags": [], "messages": []}

@app.post("/discussions/{discussion_id}/chat")
def chat_discussion(discussion_id: str, req: dict):
    """
    Chat with the discussion bot. Returns streaming response.
    """
    user_message = req.get("message", "").strip()
    if not user_message:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Message is required and cannot be empty"
        )
    
    # Limit message length to prevent abuse
    MAX_MESSAGE_LENGTH = 10000
    if len(user_message) > MAX_MESSAGE_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Message too long. Maximum length is {MAX_MESSAGE_LENGTH} characters."
        )
    
    def generate():
        try:
            # Send initial heartbeat to keep connection alive
            yield f": heartbeat\n\n"
            
            for chunk, full_text in ds.chat_in_discussion_stream(discussion_id, user_message):
                # Send chunk in SSE format: data: <chunk>\n\n
                yield f"data: {json.dumps({'chunk': chunk, 'full_text': full_text})}\n\n"
            
            # Send done signal
            yield f"data: {json.dumps({'done': True})}\n\n"
        except ValueError as e:
            # Discussion not found or validation error
            logger.error(f"Chat error for discussion {discussion_id}: {e}")
            yield f"data: {json.dumps({'error': str(e), 'type': 'validation'})}\n\n"
        except Exception as e:
            # Generic error
            logger.error(f"Unexpected error in chat stream for discussion {discussion_id}: {e}")
            yield f"data: {json.dumps({'error': 'An error occurred while generating response', 'type': 'server'})}\n\n"
            # Always send done to close stream
            yield f"data: {json.dumps({'done': True})}\n\n"
    
    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable buffering for nginx
        }
    )

@app.delete("/discussions/{discussion_id}")
def delete_discussion(discussion_id: str):
    return ds.delete_discussion(discussion_id)

@app.post("/admin/rebuild_imessage_rag")
def rebuild_imessage_rag():
    """
    Clear all indexed messages and re-import everything from chat.db.
    Use this to rebuild the RAG from scratch.
    """
    from services.rag_imessage_import import clear_and_reimport
    clear_and_reimport()
    return {"status": "ok", "message": "Cleared and re-imported all iMessage messages into RAG from chat.db."}

@app.post("/admin/sync_imessage_rag")
def sync_imessage_rag():
    """
    Incrementally import only new messages from chat.db that aren't already in RAG.
    Use this to update RAG with new messages without re-importing everything.
    """
    from services.rag_imessage_import import import_new_messages
    try:
        import_new_messages()
        # Clear analysis cache since new messages may affect analysis
        ANALYSIS_CACHE.clear()
        logger.info("Analysis cache cleared after RAG sync")
        return {"status": "ok", "message": "Synced new iMessage messages into RAG."}
    except Exception as e:
        logger.error(f"Failed to sync iMessage RAG: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to sync RAG: {e}"
        )

@app.post("/admin/clear_analysis_cache")
def clear_analysis_cache():
    """
    Clear the analysis result cache. Useful after importing new messages.
    """
    count = len(ANALYSIS_CACHE)
    ANALYSIS_CACHE.clear()
    logger.info(f"Cleared {count} cached analysis results")
    return {"status": "ok", "message": f"Cleared {count} cached analysis results."}

# ============================================================================
# Unread Messages Endpoints
# ============================================================================

@app.get("/unread", response_model=List[UnreadMessage])
def get_unread_messages():
    """
    Get all unread messages from contacts.
    Returns only incoming messages that haven't been marked as read.
    Also automatically syncs new messages (both incoming and outgoing) to RAG.
    """
    from services.unread_service import get_unread_messages
    try:
        # This will also sync new messages to RAG automatically
        unread = get_unread_messages()
        return unread
    except Exception as e:
        logger.error(f"Error getting unread messages: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get unread messages: {e}"
        )

@app.get("/unread/count")
def get_unread_count():
    """Get the total count of unread messages."""
    from services.unread_service import get_unread_messages
    try:
        unread = get_unread_messages()
        return {"count": len(unread)}
    except Exception as e:
        logger.error(f"Error getting unread count: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get unread count: {e}"
        )

@app.post("/unread/mark-read")
def mark_as_read(req: MarkReadRequest):
    """
    Mark messages for a contact as read.
    Updates the last seen message ID for the contact.
    """
    from services.unread_service import mark_contact_as_read
    try:
        mark_contact_as_read(req.contact_id, req.message_id)
        return {"status": "ok", "message": f"Marked contact {req.contact_id} as read up to message {req.message_id}"}
    except Exception as e:
        logger.error(f"Error marking contact as read: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to mark as read: {e}"
        )

@app.post("/admin/reset-unread-state")
def reset_unread_state():
    """
    Reset the unread state by marking all current messages as read.
    After this, only NEW messages will be considered unread.
    Useful if the unread state gets out of sync.
    """
    from services.unread_service import reset_unread_state
    try:
        state = reset_unread_state()
        return {"status": "ok", "message": f"Reset unread state for {len(state)} contacts. All current messages are now marked as read."}
    except Exception as e:
        logger.error(f"Error resetting unread state: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to reset unread state: {e}"
        )

# Background task to check for unread messages periodically
async def check_unread_messages_periodically():
    """
    Background task that checks for unread messages every few seconds.
    Also automatically syncs new messages (incoming and outgoing) to RAG.
    """
    from services.unread_service import get_unread_messages
    check_interval = 5  # Check every 5 seconds
    
    while True:
        try:
            await asyncio.sleep(check_interval)
            # This will check for unread messages AND sync new messages to RAG
            unread = get_unread_messages()
            if unread:
                logger.info(f"Found {len(unread)} unread message(s)")
        except Exception as e:
            logger.error(f"Error in background unread check: {e}")

