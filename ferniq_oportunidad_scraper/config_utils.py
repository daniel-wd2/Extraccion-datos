import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"
DEFAULT_COMERCIALES_FILE = BASE_DIR.parent / "comerciales.txt"


def load_dotenv(env_file: Path = ENV_FILE) -> None:
    if not env_file.exists():
        return

    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip().lstrip("\ufeff")
        value = value.strip().strip('"').strip("'")

        if key and key not in os.environ:
            os.environ[key] = value


def load_comerciales(defaults: list[str], file_path: Path = DEFAULT_COMERCIALES_FILE) -> list[str]:
    configured_path = os.getenv("FERNIQ_COMERCIALES_FILE", "").strip()
    if configured_path:
        file_path = Path(configured_path)

    if not file_path.exists():
        return defaults

    comerciales: list[str] = []
    seen: set[str] = set()
    for raw_line in file_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        normalized = " ".join(line.split()).casefold()
        if normalized in seen:
            continue
        seen.add(normalized)
        comerciales.append(" ".join(line.split()))

    return comerciales or defaults
