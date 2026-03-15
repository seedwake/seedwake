"""Embedding service using Ollama."""

from ollama import Client


def embed_text(client: Client, text: str, model: str) -> list[float]:
    """Get embedding vector for a single text."""
    response = client.embed(model=model, input=text)
    return list(response.embeddings[0])


def embed_texts(client: Client, texts: list[str], model: str) -> list[list[float]]:
    """Get embedding vectors for multiple texts."""
    response = client.embed(model=model, input=texts)
    return [list(v) for v in response.embeddings]
