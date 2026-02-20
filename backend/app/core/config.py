from pydantic_settings import BaseSettings
from pathlib import Path


class Settings(BaseSettings):
    APP_NAME: str = "DataWhisper"
    DEBUG: bool = False

    # JWT
    SECRET_KEY: str = "change-this-to-a-random-secret-key"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 480

    # Default user passwords (override via .env in production)
    ADMIN_PASSWORD: str = "Admin@2024"
    MANAGER_PASSWORD: str = "Manager@2024"

    # Account lockout
    MAX_LOGIN_ATTEMPTS: int = 5
    LOCKOUT_MINUTES: int = 15

    # Local LLM settings (Ollama)
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    LLM_MODEL: str = "llama3.2:3b"

    # Data paths
    UPLOAD_DIR: Path = Path("data/uploads")
    DATABASE_DIR: Path = Path("data/databases")

    # Max upload size in MB
    MAX_UPLOAD_SIZE_MB: int = 500

    # CORS — comma-separated list of allowed origins, or "*" for LAN access
    ALLOWED_ORIGINS: str = "*"

    class Config:
        env_file = ".env"


settings = Settings()
