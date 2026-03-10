from app.core.supabase_client import supabase
from .embedding_service import generate_embedding


def semantic_cache_lookup(question: str):

    embedding = generate_embedding(question)

    result = (
        supabase.rpc(
            "match_qa_embeddings",
            {
                "query_embedding": embedding,
                "match_count": 1,
                "match_lang": "en",
                "match_jurisdiction": "nigeria",
                "min_trust": 0.75,
            },
        )
        .execute()
    )

    if result.data:
        return result.data[0]

    return None
