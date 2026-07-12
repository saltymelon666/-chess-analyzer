from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent


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
    return os.getenv(name, defaults.get(name, fallback))


@dataclass(frozen=True)
class Settings:
    deepseek_api_key: str
    deepseek_base_url: str
    deepseek_model: str
    stockfish_path: Path
    stockfish_depth: int
    stockfish_threads: int
    stockfish_hash: int
    stockfish_multipv: int
    stockfish_timeout_seconds: float
    deepseek_timeout_seconds: float
    allowed_origins: tuple[str, ...]


def load_settings() -> Settings:
    defaults = _read_dotenv(ROOT_DIR / ".env")
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
        deepseek_api_key=_value("DEEPSEEK_API_KEY", defaults),
        deepseek_base_url=_value("DEEPSEEK_BASE_URL", defaults, "https://api.deepseek.com").rstrip("/"),
        deepseek_model=_value("DEEPSEEK_MODEL", defaults, "deepseek-chat"),
        stockfish_path=stockfish_path,
        stockfish_depth=int(_value("STOCKFISH_DEPTH", defaults, "16")),
        stockfish_threads=max(1, int(_value("STOCKFISH_THREADS", defaults, "1"))),
        stockfish_hash=max(16, int(_value("STOCKFISH_HASH", defaults, "64"))),
        stockfish_multipv=max(1, int(_value("STOCKFISH_MULTIPV", defaults, "3"))),
        stockfish_timeout_seconds=float(_value("STOCKFISH_TIMEOUT_SECONDS", defaults, "30")),
        deepseek_timeout_seconds=float(_value("DEEPSEEK_TIMEOUT_SECONDS", defaults, "30")),
        allowed_origins=origins,
    )

