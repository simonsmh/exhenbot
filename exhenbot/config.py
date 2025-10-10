import os
from dataclasses import dataclass


@dataclass
class Settings:
    local_dir: str
    task_check: str

    # ExHentai
    exh_cookie: str
    exh_semaphore_size: int
    exh_query: str
    exh_catogories: int
    exh_star: int
    exh_query_depth: int

    # File Uploader
    fileuploader_semaphore_size: int
    fileuploader_timeout: int

    # Telegraph
    telegraph_author_name: str
    telegraph_author_url: str
    telegraph_token: str

    # Database
    db_url: str

    # Telegram
    telegram_bot_token: str
    telegram_job_interval: int

    # API
    telegram_domain: str
    telegram_host: str
    telegram_port: str
    telegram_api_base_url: str
    telegram_api_base_file_url: str
    telegram_local_mode: bool
    telegram_semaphore_size: str


def load_settings() -> Settings:
    """Load settings from environment variables with reasonable defaults."""
    return Settings(
        local_dir=os.environ.get("LOCAL_DIR", ".exhenbot"),
        task_check=os.environ.get("TASK_CHECK", "exhenbot:exhenbot"),
        exh_cookie=os.environ.get("EXH_COOKIE"),
        exh_semaphore_size=int(os.environ.get("EXH_SEMAPHORE_SIZE", 4)),
        exh_query=os.environ.get("EXH_QUERY", "parody:\"blue archive$\" language:chinese$"),
        exh_catogories=int(os.environ.get("EXH_CATOGORIES", 1017)),
        exh_star=int(os.environ.get("EXH_STAR", 4)),
        exh_query_depth=int(os.environ.get("EXH_QUERY_DEPTH", 1)),
        fileuploader_semaphore_size=int(os.environ.get("FILEUPLOADER_SEMAPHORE_SIZE", 10)),
        fileuploader_timeout=int(os.environ.get("FILEUPLOADER_TIMEOUT", 30)),
        telegraph_author_name=os.environ.get("TELEGRAPH_AUTHOR_NAME", "exhenbot"),
        telegraph_author_url=os.environ.get("TELEGRAPH_AUTHOR_URL", "https://t.me/exhenbot"),
        telegraph_token=os.environ.get("TELEGRAPH_ACCESS_TOKEN"),
        db_url=os.environ.get("DATABASE_URL"),
        telegram_bot_token=os.environ.get("TELEGRAM_BOT_TOKEN"),
        telegram_job_interval=int(os.environ.get("TELEGRAM_JOB_INTERVAL", 600)),
        telegram_domain=os.environ.get("TELEGRAM_DOMAIN"),
        telegram_host=os.environ.get("TELEGRAM_HOST"),
        telegram_port=os.environ.get("TELEGRAM_PORT"),
        telegram_api_base_url=os.environ.get("TELEGRAM_API_BASE_URL", "https://api.telegram.org/bot"),
        telegram_api_base_file_url=os.environ.get("TELEGRAM_API_BASE_FILE_URL", "https://api.telegram.org/file/bot"),
        telegram_local_mode=os.environ.get("TELEGRAM_LOCAL_MODE", "false") == "true",
        telegram_semaphore_size=os.environ.get("TELEGRAM_SEMAPHORE_SIZE"),
    )
