"""Downloads the official schedules and updates data/draws/.

    python -m curlcal.refresh              update every event's draws
    python -m curlcal.refresh --teams      rebuild the Grand Slam team-country list
    python -m curlcal.refresh --check-new  fail if World Curling lists an event we don't

Exits non-zero if any source failed, so a broken parser shows up as a failed run.
Events that fail keep their previous draws.
"""
import json
import re
import sys
import time
import urllib.error
import urllib.request

from . import parsers, season

USER_AGENT = "curling-calendar/0.1"
ATTEMPTS = 3
GSOC = "https://www.thegrandslamofcurling.com"
_cache = {}


def fetch(url):
    if url not in _cache:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        for attempt in range(1, ATTEMPTS + 1):
            time.sleep(attempt)  # stay polite: these are small sites
            try:
                with urllib.request.urlopen(request, timeout=45) as response:
                    _cache[url] = response.read().decode("utf-8", errors="replace")
                break
            except (urllib.error.URLError, TimeoutError) as error:
                if attempt == ATTEMPTS:
                    raise
                print(f"retrying {url}: {error}", file=sys.stderr)
    return _cache[url]


def draws_for(event, teams):
    kind = event["source"]["type"]
    if kind == "wcf":
        return parsers.parse_wcf(fetch(event["url"]), event)
    if kind == "rock_league":
        return parsers.parse_rock_league(fetch(event["url"]), event)
    if kind == "gsoc":
        path = parsers.find_gsoc_article(fetch(event["url"]), event["source"]["keyword"])
        return parsers.parse_gsoc_article(fetch(GSOC + path), event, teams) if path else []
    raise parsers.ParseError(f"{event['id']}: unknown source type {kind!r}")


def refresh():
    teams = season.load_teams()
    failures = []
    for event in season.load_season()["event"]:
        if "source" not in event:
            continue
        try:
            draws = draws_for(event, teams)
        except (urllib.error.URLError, TimeoutError, parsers.ParseError) as error:
            failures.append(f"{event['id']}: {error}")
            continue
        if draws or not season.load_draws(event["id"]):
            # An empty result never overwrites published draws: a page that
            # temporarily loses its schedule should not wipe the calendar.
            season.save_draws(event["id"], draws)
        print(f"{event['id']}: {len(draws)} draws")
    return failures


def refresh_teams():
    listing = fetch(f"{GSOC}/teams")
    paths = sorted(set(re.findall(r"/teams/team-[a-z0-9-]+-\d+", listing)))
    teams = {}
    for path in paths:
        page = fetch(GSOC + path)
        name = re.search(r'name-two[^>]*>([^<]+)<', page)
        town = re.search(r'Hometown:</span>\s*<span[^>]*>([^<]+)<', page)
        if not name or not town:
            raise parsers.ParseError(f"{path}: name or hometown missing")
        teams[parsers._team_key(name[1])] = parsers.hometown_country(town[1])
    season.TEAMS_FILE.write_text(json.dumps(teams, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                                 encoding="utf-8")
    print(f"{len(teams)} teams")


def check_new():
    data = season.load_season()
    index = fetch("https://worldcurling.org/events/")
    listed = set(re.findall(r"worldcurling\.org/events/([a-z0-9-]+)/", index))
    known = set(data["ignored_wcf_slugs"])
    for event in data["event"]:
        known.update(re.findall(r"worldcurling\.org/events/([a-z0-9-]+)/", event["url"]))
    return [f"not on the calendar: worldcurling.org/events/{slug}/" for slug in sorted(listed - known)]


def main():
    if "--teams" in sys.argv:
        refresh_teams()
        return 0
    problems = check_new() if "--check-new" in sys.argv else refresh()
    for problem in problems:
        print(f"PROBLEM {problem}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
