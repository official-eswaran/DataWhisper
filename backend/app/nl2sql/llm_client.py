import requests
from app.core.config import settings


def call_local_llm(prompt: str) -> str:
    """Call local Ollama LLM. All data stays on your machine."""
    response = requests.post(
        f"{settings.OLLAMA_BASE_URL}/api/generate",
        json={
            "model": settings.LLM_MODEL,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": 0.1,  # Low temp for precise SQL
                "num_predict": 512,
            },
        },
        timeout=60,
    )
    response.raise_for_status()
    return response.json()["response"]
