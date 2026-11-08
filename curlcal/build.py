"""Builds the public page and calendar feeds, plus the private plan of personal-calendar events."""
import json
import re
import shutil
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import season

MADRID = ZoneInfo("Europe/Madrid")
FLAGS = {"CAN": "🇨🇦", "ESP": "🇪🇸", "TUR": "🇹🇷", "CZE": "🇨🇿"}
DTSTAMP = "20261108T000000Z"  # fixed so unchanged events produce byte-identical feeds
MAX_GAMES_IN_TITLE = 4  # a Brier draw has up to nine games; those go in the description
GSOC_NOTE = ("Which draws DAZN carries is based on the Masters schedule; "
             "this event's broadcast schedule had not been published when this was written.")


def _tracked(game, tracked):
    return [c for c in (game["a_c"], game["b_c"]) if c in tracked]


def game_entries(event, draws, tracked):
    """Calendar entries for one event: one per time slot that has a tracked team or a playoff."""
    if not any(_tracked(g, tracked) for d in draws for g in d["games"]):
        return []
    tz = ZoneInfo(event["tz"])
    slots = {}
    for draw in draws:
        games = [g for g in draw["games"] if _tracked(g, tracked)]
        is_open_playoff = draw["playoff"] and not [g for g in draw["games"] if g["a_c"] or g["b_c"]]
        if games or is_open_playoff:
            slots.setdefault(draw["local"], []).append((draw, games))

    entries = []
    for local_text, group in sorted(slots.items()):
        local = datetime.fromisoformat(local_text).replace(tzinfo=tz)
        start = local.astimezone(timezone.utc)
        games = [g for _, gs in group for g in gs]
        playoff_labels = [re.sub(r"^Draw \d+ / ", "", d["label"]) for d, gs in group if not gs]
        flags = "".join(FLAGS[c] for c in tracked
                        if any(c in _tracked(g, tracked) for g in games))
        short = event["name"].replace("World Championship Qualifier", "WCQ")
        matchups = ", ".join(f"{g['a']} v {g['b']}" for g in games)
        if event["source"]["type"] == "rock_league" and playoff_labels:
            title_core = f"{playoff_labels[0]} (teams TBD)"
        elif len(games) > MAX_GAMES_IN_TITLE:
            title_core = f"{group[0][0]['label']}, {len(games)} games"
        elif matchups:
            title_core = matchups
        elif event["source"]["type"] == "gsoc":
            title_core = " / ".join(playoff_labels)  # a Slam field always has Canadians in it
        else:
            title_core = " / ".join(playoff_labels) + " (if qualified)"
        summary = f"{flags + ' ' if flags else ''}{title_core} · {short}"

        watch_key = event["watch"]
        note = GSOC_NOTE if watch_key == "gsoc" else ""
        entries.append({
            "id": group[0][0]["id"],
            "event": event["id"],
            "summary": summary,
            "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end": (start + timedelta(hours=event["game_hours"])).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "city": event["city"],
            "watch_key": watch_key,
            "note": note,
            "countries": [c for c in tracked if flags and FLAGS[c] in flags],
            "tier": event["tier"],
            "lines": [f"{g['a']} v {g['b']}" for g in games] + playoff_labels,
            "url": event["url"],
        })
    return entries


def _escape(text):
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _fold(line):
    """Folds to 75 octets per RFC 5545 without splitting a multi-byte character."""
    out, current, size = [], "", 0
    for ch in line:
        width = len(ch.encode("utf-8"))
        if size + width > 75:
            out.append(current)
            current, size = " ", 1
        current += ch
        size += width
    out.append(current)
    return "\r\n".join(out)


def _describe(lines, watch, note, url):
    parts = list(lines) + ["", f"Watch: {watch}"]
    if note:
        parts.append(note)
    parts += ["", url]
    return "\n".join(parts).strip()


def _vevent(uid, when_lines, summary, location, description):
    lines = ["BEGIN:VEVENT", f"UID:{uid}@curling-calendar", f"DTSTAMP:{DTSTAMP}", *when_lines,
             f"SUMMARY:{_escape(summary)}", f"LOCATION:{_escape(location)}",
             f"DESCRIPTION:{_escape(description)}", "TRANSP:TRANSPARENT", "END:VEVENT"]
    return [_fold(line) for line in lines]


def _block_vevent(block):
    start, stop = block["start"].replace("-", ""), block["end_exclusive"].replace("-", "")
    when = [f"DTSTART;VALUE=DATE:{start}", f"DTEND;VALUE=DATE:{stop}"]
    return _vevent(block["id"], when, block["summary"], block["city"], block["description"])


def _game_vevent(entry, watch):
    fmt = lambda text: text.replace("-", "").replace(":", "")
    when = [f"DTSTART:{fmt(entry['start'])}", f"DTEND:{fmt(entry['end'])}"]
    description = _describe(entry["lines"], watch, entry["note"], entry["url"])
    return _vevent(entry["id"], when, entry["summary"], entry["city"], description)


def _calendar(name, body):
    head = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//curling-calendar//EN", "CALSCALE:GREGORIAN",
            "METHOD:PUBLISH", f"X-WR-CALNAME:{name}", "REFRESH-INTERVAL;VALUE=DURATION:PT6H",
            "X-PUBLISHED-TTL:PT6H"]
    return "\r\n".join(head + body + ["END:VCALENDAR", ""])


def collect():
    data = season.load_season()
    blocks, games = [], []
    for event in data["event"]:
        watch = data["watch"][event["watch"]]
        blocks.append({
            "id": f"block-{event['id']}",
            "event": event["id"],
            "summary": f"Curling: {event['name']}",
            "name": event["name"],
            "start": event["start"].isoformat(),
            "end": event["end"].isoformat(),
            "end_exclusive": (event["end"] + timedelta(days=1)).isoformat(),
            "city": event["city"],
            "tier": event["tier"],
            "watch": watch,
            "url": event["url"],
            "description": _describe([event["name"], event["city"]], watch, "", event["url"]),
        })
        if "source" in event:
            games += game_entries(event, season.load_draws(event["id"]), data["tracked"])
    games.sort(key=lambda g: g["start"])
    return data, blocks, games


def personal_plan(data, blocks, games, today):
    """The events for the personal Google calendar, written through the Calendar connector.

    Each description ends with a "ref:" line. That key is how a later sync recognises the event.
    """
    plan = []
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for block in blocks:
        if date.fromisoformat(block["end"]) < today:
            continue
        plan.append({"key": block["id"], "summary": block["summary"], "allDay": True,
                     "startTime": f"{block['start']}T00:00:00",
                     "endTime": f"{block['end_exclusive']}T00:00:00",
                     "location": block["city"],
                     "description": f"{block['description']}\n\nref: {block['id']}",
                     "reminders": [{"method": "popup", "minutes": 900}]})  # 09:00 the day before
    for game in games:
        if game["tier"] != 1 or game["start"] < now_utc:
            continue
        start = datetime.fromisoformat(game["start"]).astimezone(MADRID)
        end = datetime.fromisoformat(game["end"]).astimezone(MADRID)
        description = _describe(game["lines"], data["watch"][game["watch_key"]], game["note"], game["url"])
        plan.append({"key": game["id"], "summary": game["summary"], "allDay": False,
                     "startTime": start.strftime("%Y-%m-%dT%H:%M:%S"),
                     "endTime": end.strftime("%Y-%m-%dT%H:%M:%S"),
                     "location": game["city"],
                     "description": f"{description}\n\nref: {game['id']}",
                     "reminders": [{"method": "popup", "minutes": 30}]})
    return plan


def main(today=None):
    today = today or date.today()
    data, blocks, games = collect()
    public, local = season.PUBLIC_DIR, season.LOCAL_DIR
    public.mkdir(exist_ok=True)
    local.mkdir(exist_ok=True)

    block_body = [line for b in blocks for line in _block_vevent(b)]
    game_body = [line for g in games for line in _game_vevent(g, data["watch"][g["watch_key"]])]
    feeds = {
        "tournaments.ics": ("Curling tournaments", block_body),
        "games.ics": ("Curling games (Canada, Spain, Türkiye, Czechia)", game_body),
        "all.ics": ("Curling on TV", block_body + game_body),
    }
    for filename, (name, body) in feeds.items():
        (public / filename).write_bytes(_calendar(name, body).encode("utf-8"))

    site = {"blocks": blocks, "games": games, "watch": data["watch"]}
    embedded = json.dumps(site, ensure_ascii=False).replace("</", "<\\/")
    template = (season.ROOT / "site" / "template.html").read_text(encoding="utf-8")
    if "/*DATA*/null" not in template:
        raise ValueError("site/template.html is missing the /*DATA*/null placeholder")
    (public / "index.html").write_text(template.replace("/*DATA*/null", embedded), encoding="utf-8")

    plan = personal_plan(data, blocks, games, today)
    (local / "google_plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(blocks)} tournaments, {len(games)} game entries, {len(plan)} on the personal plan")


if __name__ == "__main__":
    main()
