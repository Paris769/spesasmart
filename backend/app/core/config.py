from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql+asyncpg://spesasmart:spesasmart_dev@localhost:5432/spesasmart"
    REDIS_URL: str = "redis://localhost:6379/0"
    MEILI_URL: str = "http://localhost:7700"
    MEILI_MASTER_KEY: str = "spesasmart_search_key"
    SECRET_KEY: str = "change_this_in_production_32chars"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30
    PEPESTO_API_KEY: str = ""
    PRISY_API_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""

    class Config:
        env_file = ".env"


settings = Settings()
