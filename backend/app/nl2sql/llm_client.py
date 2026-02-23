import json as _json
import requests
from requests.exceptions import ConnectionError as RequestsConnectionError, Timeout
from app.core.config import settings


def call_local_llm(prompt: str) -> str:
    """Call local Ollama LLM (non-streaming). All data stays on your machine."""
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


def stream_local_llm(prompt: str):
    """
    Stream tokens from Ollama one-by-one.
    Yields (token: str) as they arrive.
    Raises RuntimeError on connection/timeout issues.
    Also returns the full assembled response via the final yielded value
    which is a special sentinel tuple ("__done__", full_text).
    """
    try:
        response = requests.post(
            f"{settings.OLLAMA_BASE_URL}/api/generate",
            json={
                "model": settings.LLM_MODEL,
                "prompt": prompt,
                "stream": True,
                "options": {
                    "temperature": 0.1,
                    "num_predict": 512,
                },
            },
            stream=True,
            timeout=60,
        )
        response.raise_for_status()
    except RequestsConnectionError:
        raise RuntimeError("Ollama is not running. Start it with: ollama serve")
    except Timeout:
        raise RuntimeError("Ollama request timed out. The model may still be loading.")
    except Exception as e:
        raise RuntimeError(f"LLM error: {str(e)}")

    full_text = ""
    for raw_line in response.iter_lines():
        if not raw_line:
            continue
        try:
            chunk = _json.loads(raw_line)
        except Exception:
            continue
        token = chunk.get("response", "")
        if token:
            full_text += token
            yield token
        if chunk.get("done"):
            break

    # Sentinel: lets the consumer know generation is complete + get full text
    yield ("__done__", full_text)
