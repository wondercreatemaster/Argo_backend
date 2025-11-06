import os, chromadb
from chromadb.utils import embedding_functions

# We'll pass precomputed embeddings to Chroma; but also expose OpenAIEmbeddingFunction if you prefer.
def get_chroma_client():
    persist_dir = os.getenv("CHROMA_DIR", "./chroma_store")
    os.makedirs(persist_dir, exist_ok=True)
    return chromadb.PersistentClient(path=persist_dir)

def get_collection(name="messages"):
    client = get_chroma_client()
    try:
        return client.get_collection(name)
    except Exception:
        return client.create_collection(name)

def add_messages(docs: list[str], ids: list[str], metadatas: list[dict], embeddings: list[list[float]]):
    col = get_collection()
    col.add(documents=docs, ids=ids, metadatas=metadatas, embeddings=embeddings)

def query_by_contact(contact_id: str, query_embedding: list[float], top_k: int = 12):
    col = get_collection()
    res = col.query(
        query_embeddings=[query_embedding],
        n_results=top_k,
        where={"contact_id": contact_id}  # filter by person
    )
    # Flatten into list of dicts
    hits = []
    for i in range(len(res["ids"][0])):
        hits.append({
            "id": res["ids"][0][i],
            "document": res["documents"][0][i],
            "metadata": res["metadatas"][0][i],
            "distance": res["distances"][0][i] if "distances" in res else None
        })
    return hits
