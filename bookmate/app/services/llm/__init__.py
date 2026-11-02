"""在线大模型能力：Chat / Embedding / Rerank（全部 OpenAI 兼容 API）。"""
from app.services.llm.client import LLMClient, close_http_client, get_llm_client
from app.services.llm.embeddings import embed_one, embed_texts
from app.services.llm.rerank import rerank_documents

__all__ = [
    "LLMClient",
    "close_http_client",
    "embed_one",
    "embed_texts",
    "get_llm_client",
    "rerank_documents",
]
