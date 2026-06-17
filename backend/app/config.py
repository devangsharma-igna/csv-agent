from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    AZURE_OPENAI_ENDPOINT: str
    AZURE_OPENAI_API_KEY: str
    AZURE_OPENAI_API_VERSION: str = "2024-10-21"
    AZURE_OPENAI_DEPLOYMENT: str = "gpt-4.1"

    CONTEXT_DIR: str = "../context"
    DATA_DIR: str = "../data"
    FRONTEND_ORIGIN: str = "http://localhost:5173"

    MAX_REACT_ITERATIONS: int = 8
    MAX_SQL_RETRIES: int = 2
    NL_PARSER_MAX_COLUMNS: int = 25
    LOG_LEVEL: str = "INFO"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def context_path(self) -> Path:
        p = Path(self.CONTEXT_DIR).resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def data_path(self) -> Path:
        p = Path(self.DATA_DIR).resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p


settings = Settings()
