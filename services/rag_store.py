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

def query_chat_history(query_embedding: list[float], top_k: int = 10):
    """
    Query chat history from chat.db (messages collection) by semantic similarity.
    Returns relevant chat history messages that match the query.
    """
    col = get_collection()
    try:
        res = col.query(
            query_embeddings=[query_embedding],
            n_results=top_k,
            # No contact_id filter - search across all chat history
        )
        # Flatten into list of dicts
        hits = []
        if res.get("ids") and len(res["ids"]) > 0 and len(res["ids"][0]) > 0:
            for i in range(len(res["ids"][0])):
                hits.append({
                    "id": res["ids"][0][i],
                    "document": res["documents"][0][i],
                    "metadata": res["metadatas"][0][i],
                    "distance": res["distances"][0][i] if "distances" in res else None
                })
        return hits
    except Exception as e:
        print(f"⚠️ Error querying chat history: {e}")
        return []

def get_existing_message_ids() -> set:
    """Get set of all existing message IDs from RAG collection."""
    col = get_collection()
    try:
        # Get all IDs - we'll use peek with a large limit
        # ChromaDB doesn't have a direct "get all" but we can use query with limit
        results = col.get(limit=100000)  # Large limit to get all
        return set(results["ids"])
    except Exception:
        return set()

def clear_all_messages():
    """Clear all indexed messages from RAG collection."""
    col = get_collection()
    try:
        # Get all IDs first
        all_results = col.get(limit=100000)
        if all_results["ids"]:
            col.delete(ids=all_results["ids"])
            return len(all_results["ids"])
        return 0
    except Exception as e:
        print(f"⚠️ Error clearing RAG: {e}")
        # Try alternative: delete collection and recreate
        try:
            client = get_chroma_client()
            client.delete_collection("messages")
            return 0
        except Exception as e2:
            print(f"⚠️ Error deleting collection: {e2}")
            raise

def has_indexed_messages() -> bool:
    """Check if RAG collection has any indexed messages."""
    col = get_collection()
    try:
        count = col.count()
        return count > 0
    except Exception:
        return False
