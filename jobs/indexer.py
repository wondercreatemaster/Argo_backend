import os, json, tqdm
from dotenv import load_dotenv
from services.openai_bridge import embed_texts
from services.rag_store import add_messages

load_dotenv()
DATA = os.path.join(os.path.dirname(__file__), "..", "data", "chat_history.json")

def normalize_msg(contact_id: str, ts: str, direction: str, text: str) -> str:
    who = "IN" if direction == "in" else "OUT"
    return f"[{ts}] {who}: {text.strip()}"

def run(batch_size=128):
    with open(DATA, "r", encoding="utf-8") as f:
        contacts = json.load(f)

    docs, ids, metas = [], [], []
    for c in contacts:
        cid = c["contact_id"]
        name = c.get("display_name", cid)
        for idx, m in enumerate(c.get("messages", [])):
            docs.append(normalize_msg(cid, m["ts"], m["direction"], m["text"]))
            ids.append(f"{cid}::{idx}")
            metas.append({"contact_id": cid, "display_name": name, "ts": m["ts"], "direction": m["direction"]})

    # Chunk-embed to avoid huge payloads
    for i in tqdm.tqdm(range(0, len(docs), batch_size)):
        chunk_docs  = docs[i:i+batch_size]
        chunk_ids   = ids[i:i+batch_size]
        chunk_metas = metas[i:i+batch_size]
        embs = embed_texts(chunk_docs)  # list[list[float]]
        add_messages(chunk_docs, chunk_ids, chunk_metas, embs)

if __name__ == "__main__":
    run()
