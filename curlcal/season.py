"""Loads the season registry and the generated draw files."""
import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEASON_FILE = ROOT / "data" / "season.toml"
DRAWS_DIR = ROOT / "data" / "draws"
TEAMS_FILE = ROOT / "data" / "gsoc_teams.json"
PUBLIC_DIR = ROOT / "public"
LOCAL_DIR = ROOT / ".local"


def load_season():
    with SEASON_FILE.open("rb") as f:
        return tomllib.load(f)


def load_draws(event_id):
    """Returns the saved draws for an event, or [] if none have been published."""
    path = DRAWS_DIR / f"{event_id}.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def save_draws(event_id, draws):
    DRAWS_DIR.mkdir(parents=True, exist_ok=True)
    text = json.dumps(draws, ensure_ascii=False, indent=1) + "\n"
    (DRAWS_DIR / f"{event_id}.json").write_text(text, encoding="utf-8")


def load_teams():
    return json.loads(TEAMS_FILE.read_text(encoding="utf-8"))
