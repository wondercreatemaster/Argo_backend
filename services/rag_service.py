# services/rag_service.py
import os
import logging
import chromadb
from chromadb.utils import embedding_functions
from services.openai_bridge import embed_texts

logger = logging.getLogger(__name__)

CHROMA_PATH = "data/chroma"

# Initialize ChromaDB client
chroma_client = chromadb.PersistentClient(path=CHROMA_PATH)
collection = chroma_client.get_or_create_collection("discussions")

def add_to_rag(discussion_id: str, text: str, role: str):
    """
    Store a message (from user or Argo) in the vector DB.
    """
    emb = embed_texts([text])[0]
    collection.add(
        ids=[f"{discussion_id}-{os.urandom(6).hex()}"],
        embeddings=[emb],
        documents=[text],
        metadatas=[{"discussion": discussion_id, "role": role}],
    )

def query_rag(query: str, top_k: int = 5):
    """
    Retrieve top related discussion snippets.
    """
    q_emb = embed_texts([query])[0]
    results = collection.query(
        query_embeddings=[q_emb],
        n_results=top_k,
    )
    return results

def delete_from_rag(discussion_id: str):
    """
    Delete all embeddings related to a given discussion ID from the RAG collection.
    """
    try:
        count_before = collection.count()
        collection.delete(where={"discussion": discussion_id})
        count_after = collection.count()
        logger.info(f"Deleted RAG entries for discussion {discussion_id} "
                    f"({count_before - count_after} items removed).")
    except Exception as e:
        logger.warning(f"Failed to delete RAG entries for {discussion_id}: {e}")
