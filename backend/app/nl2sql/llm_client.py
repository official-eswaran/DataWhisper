import requests
from requests.exceptions import ConnectionError as RequestsConnectionError, Timeout
from app.core.config import settings


def call_local_llm(prompt: str) -> str:
    """Call local Ollama LLM. All data stays on your machine."""
    try:
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
    except RequestsConnectionError:
        raise RuntimeError(
            "Ollama is not running. Start it with: ollama serve"
        )
    except Timeout:
        raise RuntimeError(
            "Ollama request timed out. The model may still be loading — try again."
        )
    except Exception as e:
        raise RuntimeError(f"LLM error: {str(e)}")
