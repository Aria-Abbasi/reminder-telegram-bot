from __future__ import annotations

import os
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    telegram_bot_token: str = ""
    webapp_url: str = ""
    
    # OmniRoute AI configuration
    omniroute_base_url: str = "http://127.0.0.1:20128/v1"
    omniroute_api_key: str = ""
    omniroute_model: str = "combo"
    
    # Server and storage
    host: str = "0.0.0.0"
    port: int = 8080
    database_path: str = "data/reminders.db"
    default_timezone: str = "Asia/Tehran"
    admin_user_ids: str = ""
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
    
    @property
    def env_admin_ids(self) -> set[int]:
        ids = set()
        if self.admin_user_ids:
            for part in self.admin_user_ids.split(","):
                part = part.strip()
                if part.isdigit():
                    ids.add(int(part))
        return ids

    @property
    def db_path(self) -> Path:
        path = Path(self.database_path)
        if not path.is_absolute():
            path = Path.cwd() / path
        path.parent.mkdir(parents=True, exist_ok=True)
        return path


settings = Settings()


def is_token_configured() -> bool:
    import re
    return bool(
        settings.telegram_bot_token
        and re.match(r"^\d+:[A-Za-z0-9_-]{20,}$", settings.telegram_bot_token.strip())
    )
