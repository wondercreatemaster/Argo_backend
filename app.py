import os, json
from typing import List, Dict
from fastapi import FastAPI, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from models import Contact, ContactListItem, AnalysisResult, AnalysisRequest
from services.analysis_service import analyze_contact
from services import discussions_service as ds
from db import init_db
init_db()

load_dotenv()

app = FastAPI(title="Argo Backend", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # dev-friendly; tighten later
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DATA_PATH = os.path.join(os.path.dirname(__file__), "data", "chat_history.json")
with open(DATA_PATH, "r", encoding="utf-8") as f:
    RAW = json.load(f)

CONTACTS: Dict[str, Contact] = {c["contact_id"]: Contact(**c) for c in RAW}
ANALYSIS_CACHE: Dict[str, AnalysisResult] = {}

def snip(t: str, n=96) -> str:
    t = t.replace("\n"," ").strip()
    return t if len(t)<=n else (t[:n]+"…")

@app.get("/contacts", response_model=List[ContactListItem])
def contacts_list():
    items: List[ContactListItem] = []
    for c in CONTACTS.values():
        last_ts = c.messages[-1].ts if c.messages else None
        last_snip = snip(c.messages[-1].text) if c.messages else None
        items.append(ContactListItem(
            contact_id=c.contact_id,
            display_name=c.display_name,
            last_message_ts=last_ts,
            last_message_snippet=last_snip,
            total_messages=len(c.messages)
        ))
    items.sort(key=lambda x: x.last_message_ts or "", reverse=True)
    return items

@app.get("/contacts/{contact_id}", response_model=Contact)
def contact_detail(contact_id: str):
    c = CONTACTS.get(contact_id)
    if not c: raise HTTPException(404, "Contact not found")
    return c

@app.post("/contacts/{contact_id}/analyze", response_model=AnalysisResult)
def contact_analyze(contact_id: str, req: AnalysisRequest = Body(default=AnalysisRequest())):
    if contact_id not in CONTACTS:
        raise HTTPException(404, "Contact not found")
    if contact_id in ANALYSIS_CACHE:
        return ANALYSIS_CACHE[contact_id]
    c = CONTACTS[contact_id]
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
    return {"reply": ds.chat_in_discussion(discussion_id, req["message"])}

@app.delete("/discussions/{discussion_id}")
def delete_discussion(discussion_id: str):
    return ds.delete_discussion(discussion_id)

@app.post("/admin/rebuild_imessage_rag")
def rebuild_imessage_rag():
    from services.rag_imessage_import import import_imessage_history
    import_imessage_history()
    return {"status": "ok", "message": "Imported iMessage messages into RAG."}
