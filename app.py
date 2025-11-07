import os, json
from typing import List, Dict
from fastapi import FastAPI, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from dotenv import load_dotenv
from models import Contact, ContactListItem, AnalysisResult, AnalysisRequest, Message
from services.analysis_service import analyze_contact
from services import discussions_service as ds
from db import init_db
from services import imessage_service as imsg
init_db()

load_dotenv()

app = FastAPI(title="Argo Backend", version="0.1.0")

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
            print("📊 RAG already contains indexed messages. Checking for new messages...")
            import_new_messages()  # Only import new messages
        else:
            print("🔄 RAG is empty. Starting initial import of chat history...")
            from services.rag_imessage_import import import_imessage_history_from_db
            import_imessage_history_from_db(incremental=False)  # Full import on first run
        print("✅ Chat history sync completed.")
    except Exception as e:
        print(f"⚠️ Warning: Failed to sync chat history on startup: {e}")
        print("   You can manually trigger import via /admin/rebuild_imessage_rag endpoint")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # dev-friendly; tighten later
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

ANALYSIS_CACHE: Dict[str, AnalysisResult] = {}

def snip(t: str, n=96) -> str:
    t = t.replace("\n"," ").strip()
    return t if len(t)<=n else (t[:n]+"…")

@app.get("/contacts")
def contacts_list():
    return imsg.list_contacts()

@app.get("/contacts/{chat_id}")
def contact_detail(chat_id: int):
    return imsg.get_conversation(chat_id)

@app.post("/contacts/{contact_id}/analyze", response_model=AnalysisResult)
def contact_analyze(contact_id: str, req: AnalysisRequest = Body(default=AnalysisRequest())):
    try:
        convo = imsg.get_conversation(int(contact_id))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {e}")

    if not convo or not convo.get("messages"):
        raise HTTPException(status_code=404, detail="No messages found for this contact")

    # ✅ caching for speed
    if contact_id in ANALYSIS_CACHE:
        return ANALYSIS_CACHE[contact_id]

    # Convert dict to Contact object
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
    return result

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
    user_message = req.get("message", "")
    if not user_message:
        raise HTTPException(status_code=400, detail="Message is required")
    
    def generate():
        try:
            for chunk, full_text in ds.chat_in_discussion_stream(discussion_id, user_message):
                # Send chunk in SSE format: data: <chunk>\n\n
                yield f"data: {json.dumps({'chunk': chunk, 'full_text': full_text})}\n\n"
            # Send done signal
            yield f"data: {json.dumps({'done': True})}\n\n"
        except Exception as e:
            # Send error
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
    
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
    import_new_messages()
    return {"status": "ok", "message": "Synced new iMessage messages into RAG."}
