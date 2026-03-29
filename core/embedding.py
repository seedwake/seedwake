"""Embedding service using the configured model provider."""

from core.model_client import ModelClient


def embed_text(client: ModelClient, text: str, model: str) -> list[float]:
    """Get embedding vector for a single text."""
    return client.embed_text(text, model)


def embed_texts(client: ModelClient, texts: list[str], model: str) -> list[list[float]]:
    """Get embedding vectors for multiple texts."""
    return client.embed_texts(texts, model)
