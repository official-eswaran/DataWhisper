from pydantic_settings import BaseSettings
from pathlib import Path


class Settings(BaseSettings):
    APP_NAME: str = "DataWhisper"
    SECRET_KEY: str = "change-this-to-a-random-secret-key"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 480

    # Local LLM settings (Ollama)
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    LLM_MODEL: str = "llama3.2:3b"

    # Data paths
    UPLOAD_DIR: Path = Path("data/uploads")
    DATABASE_DIR: Path = Path("data/databases")

    # Max upload size in MB
    MAX_UPLOAD_SIZE_MB: int = 500

    class Config:
        env_file = ".env"


settings = Settings()
