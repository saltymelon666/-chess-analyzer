from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
OFFICIAL_DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEFAULT_DEEPSEEK_MODEL = "deepseek-v4-flash"
LEGACY_DEEPSEEK_MODELS = {"deepseek-chat", "deepseek-reasoner"}


def _read_dotenv(path: Path) -> dict[str, str]:
    """Read a small .env file without overriding real environment variables."""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _value(name: str, defaults: dict[str, str], fallback: str = "") -> str:
    return os.getenv(name, defaults.get(name, fallback)).strip()


@dataclass(frozen=True)
class Settings:
    environment: str
    deepseek_api_key: str
    deepseek_base_url: str
    deepseek_model: str
    stockfish_path: Path
    stockfish_depth: int
    stockfish_threads: int
    stockfish_hash: int
    stockfish_multipv: int
    stockfish_timeout_seconds: float
    game_analysis_depth: int
    game_analysis_timeout_seconds: float
    game_analysis_max_plies: int
    deepseek_timeout_seconds: float
    allowed_origins: tuple[str, ...]
    analytics_database_path: Path
    analytics_database_url: str
    analytics_persistent_storage: bool
    admin_statistics_key: str
    deepseek_input_price_per_million: float
    deepseek_output_price_per_million: float


def load_settings() -> Settings:
    defaults = _read_dotenv(ROOT_DIR / ".env")
    deepseek_api_key = _value("DEEPSEEK_API_KEY", defaults)
    deepseek_base_url = _value("DEEPSEEK_BASE_URL", defaults, OFFICIAL_DEEPSEEK_BASE_URL).rstrip("/")
    deepseek_model = _value("DEEPSEEK_MODEL", defaults, DEFAULT_DEEPSEEK_MODEL)
    if deepseek_model in LEGACY_DEEPSEEK_MODELS:
        deepseek_model = DEFAULT_DEEPSEEK_MODEL
    stockfish_raw = _value("STOCKFISH_PATH", defaults, "stockfish.exe")
    stockfish_path = Path(stockfish_raw)
    if not stockfish_path.is_absolute():
        stockfish_path = ROOT_DIR / stockfish_path

    origins_raw = _value(
        "ALLOWED_ORIGINS",
        defaults,
        "http://localhost:8080,http://127.0.0.1:8080",
    )
    origins = tuple(item.strip() for item in origins_raw.split(",") if item.strip())

    return Settings(
        environment=_value("APP_ENV", defaults, "development").lower(),
        deepseek_api_key=deepseek_api_key,
        deepseek_base_url=deepseek_base_url,
        deepseek_model=deepseek_model,
        stockfish_path=stockfish_path,
        stockfish_depth=int(_value("STOCKFISH_DEPTH", defaults, "16")),
        stockfish_threads=max(1, int(_value("STOCKFISH_THREADS", defaults, "1"))),
        stockfish_hash=max(16, int(_value("STOCKFISH_HASH", defaults, "64"))),
        stockfish_multipv=max(1, int(_value("STOCKFISH_MULTIPV", defaults, "3"))),
        stockfish_timeout_seconds=float(_value("STOCKFISH_TIMEOUT_SECONDS", defaults, "30")),
        game_analysis_depth=max(6, int(_value("GAME_ANALYSIS_DEPTH", defaults, "10"))),
        game_analysis_timeout_seconds=float(_value("GAME_ANALYSIS_TIMEOUT_SECONDS", defaults, "240")),
        game_analysis_max_plies=min(200, max(1, int(_value("GAME_ANALYSIS_MAX_PLIES", defaults, "200")))),
        deepseek_timeout_seconds=float(_value("DEEPSEEK_TIMEOUT_SECONDS", defaults, "30")),
        allowed_origins=origins,
        analytics_database_path=Path(
            _value("ANALYTICS_DB_PATH", defaults, str(ROOT_DIR / "data" / "analytics.sqlite3"))
        ),
        analytics_database_url=_value("ANALYTICS_DATABASE_URL", defaults),
        analytics_persistent_storage=_value(
            "ANALYTICS_PERSISTENT_STORAGE", defaults, "false"
        ).lower() in {"1", "true", "yes", "on"},
        admin_statistics_key=_value("ADMIN_STATISTICS_KEY", defaults),
        deepseek_input_price_per_million=max(
            0, float(_value("DEEPSEEK_INPUT_PRICE_PER_MILLION", defaults, "0"))
        ),
        deepseek_output_price_per_million=max(
            0, float(_value("DEEPSEEK_OUTPUT_PRICE_PER_MILLION", defaults, "0"))
        ),
    )
