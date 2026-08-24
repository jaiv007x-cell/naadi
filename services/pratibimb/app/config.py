from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    pratibimb_host: str = "0.0.0.0"
    pratibimb_port: int = 8100
    log_level: str = "INFO"

    llm_base_url: str = "http://localhost:11434/v1"
    llm_api_key: str = "sk-local"
    llm_model: str = "llama3.1:8b"
    judge_model: str = "llama3.1:8b"

    dhaara_url: str = "http://localhost:8200"
    speech_mode: str = "stub"
    redis_url: str = "redis://localhost:6379/0"

    jwt_secret: str = "dev-only-change-in-production"
    jwt_algorithm: str = "HS256"

    auth_mode: str = "development"
    consent_store: str = "in_memory"
    dev_header_auth_enabled: bool = True


settings = Settings()
