import json
import os
import platform
import shutil
import time
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / "runs"
PRODUCTS = ROOT / "products"

_CHROME_CANDIDATES = {
    "Windows": [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    ],
    "Darwin": ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"],
    "Linux": ["/usr/bin/google-chrome", "/usr/bin/google-chrome-stable", "/opt/google/chrome/chrome"],
}


def find_chrome() -> str | None:
    if env := os.environ.get("OUTREACH_CHROME"):
        return env if Path(env).exists() else None
    for candidate in _CHROME_CANDIDATES.get(platform.system(), []):
        if Path(candidate).exists():
            return candidate
    return shutil.which("google-chrome") or shutil.which("chrome")


@contextmanager
def locked(path: Path, timeout: float = 60.0):
    """Cross-process lock via an exclusively created lock file (parallel research agents share run files)."""
    lock = path.with_name(path.name + ".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            if time.monotonic() > deadline:
                raise SystemExit(f"kilit alınamadı: {lock} (takılı kaldıysa sil)")
            time.sleep(0.05)
    try:
        yield
    finally:
        os.close(fd)
        lock.unlink(missing_ok=True)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def today() -> date:
    return datetime.now(timezone.utc).date()


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def run_dir(run_id: str) -> Path:
    path = RUNS / run_id
    if not (path / "run.json").exists():
        raise SystemExit(f"Run bulunamadı: {run_id} (önce `python -m outreach new-run` çalıştır)")
    return path


def product_dir(slug: str) -> Path:
    path = PRODUCTS / slug
    if not (path / "profile.toml").exists():
        raise SystemExit(f"Ürün paketi bulunamadı: products/{slug}/profile.toml")
    return path


def load_profile(slug: str) -> dict:
    import tomllib

    with (product_dir(slug) / "profile.toml").open("rb") as f:
        return tomllib.load(f)
