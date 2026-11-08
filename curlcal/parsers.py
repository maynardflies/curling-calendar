"""Turns the official schedule pages into a common list of draws.

A draw is {"id", "local", "label", "playoff", "games": [{"a", "b", "a_c", "b_c"}]}.
"local" is venue-local ISO time. a_c and b_c are three-letter country codes, or "" if unknown.
A page that has no schedule yet returns []; a page whose layout has changed raises ParseError.
"""
import html as html_lib
import re
from datetime import date, datetime
from html.parser import HTMLParser
from zoneinfo import ZoneInfo

MONTHS = {
    "January": 1, "February": 2, "March": 3, "April": 4, "May": 5, "June": 6,
    "July": 7, "August": 8, "September": 9, "October": 10, "November": 11, "December": 12,
}
SHORT_MONTHS = {name[:3]: number for name, number in MONTHS.items()}
PROVINCES = "AB|BC|MB|NB|NL|NS|NT|NU|ON|PE|QC|SK|YT"
PLAYOFF_WORDS = re.compile(r"qualif|semi|bronze|gold|medal|final|tiebreak", re.I)
PLACEHOLDER = re.compile(r"qualifier|tbd|tbc", re.I)
FRANCHISE_COUNTRY = {"Maple United": "CAN", "Shield Curling Club": "CAN"}


class ParseError(Exception):
    """A source page no longer has the layout the parser expects."""


def _slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _draw(event_id, local, label, games, playoff):
    stamp = local.replace("-", "").replace("T", "-").replace(":", "")
    suffix = f"-{_slug(label)}" if label else ""
    return {"id": f"{event_id}-{stamp}{suffix}", "local": local, "label": label,
            "playoff": playoff, "games": games}


def _year_for(month, event_start):
    """Schedules omit the year, so it comes from the event, allowing for a New Year rollover."""
    return event_start.year + 1 if month < event_start.month else event_start.year


def _text_lines(page):
    page = re.sub(r"(?is)<(script|style|svg)[^>]*>.*?</\1>", "", page)
    page = re.sub(r"(?i)<(/?)(tr|li|h[1-6]|table|div)\b[^>]*>", "\n", page)
    page = re.sub(r"<[^>]+>", " ", page)
    lines = (re.sub(r"\s+", " ", html_lib.unescape(line)).strip() for line in page.split("\n"))
    return [line for line in lines if line]


def parse_wcf(page, event):
    """Parses the Schedule section of a worldcurling.org event page."""
    lines = _text_lines(page)
    headings = [i for i, line in enumerate(lines) if line == "Schedule"]
    if not headings:
        raise ParseError(f"{event['id']}: no Schedule section on the page")
    body = lines[headings[-1] + 1:]

    draws, day, slot = [], None, None

    def close_slot():
        nonlocal slot
        if slot is None:
            return
        time_text, label, codes = slot
        slot = None
        if len(codes) % 2:
            raise ParseError(f"{event['id']}: odd number of teams at {day} {time_text}")
        games = [{"a": a, "b": b, "a_c": a if a != "TBC" else "", "b_c": b if b != "TBC" else ""}
                 for a, b in zip(codes[::2], codes[1::2])]
        draws.append(_draw(event["id"], f"{day.isoformat()}T{time_text}", label, games,
                           bool(PLAYOFF_WORDS.search(label))))

    for line in body:
        if line.startswith(("Historic", "Home /")):
            break
        if line in ("–", "+"):
            continue
        date_match = re.fullmatch(r"(\d{1,2}) ([A-Z][a-z]+)", line)
        if date_match and date_match[2] in MONTHS:
            close_slot()
            month = MONTHS[date_match[2]]
            day = date(_year_for(month, event["start"]), month, int(date_match[1]))
            continue
        time_match = re.fullmatch(r"(\d{1,2}:\d{2})(?:\s*\(([^)]*)\)?)?", line)
        if time_match:
            close_slot()
            if day is None:
                raise ParseError(f"{event['id']}: time {line!r} before any date")
            hours, minutes = time_match[1].split(":")
            slot = (f"{int(hours):02d}:{minutes}", (time_match[2] or "").strip(), [])
            continue
        if slot is not None and re.fullmatch(r"[A-Z]{3}", line):
            slot[2].append(line)
            continue
        raise ParseError(f"{event['id']}: unexpected schedule line {line!r}")
    close_slot()
    return draws


class _TableReader(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows, self._row, self._cell = [], None, None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None:
            self._row.append(re.sub(r"\s+", " ", "".join(self._cell)).strip())
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def _team_key(name):
    swaps = {"ä": "ae", "ö": "oe", "ü": "ue", "å": "a", "é": "e"}
    return "".join(swaps.get(ch, ch) for ch in name.lower()).strip()


def _gsoc_country(name, event, teams):
    if PLACEHOLDER.search(name):
        return ""
    key = _team_key(name)
    if key not in teams:
        raise ParseError(f"{event['id']}: team {name!r} is not in gsoc_teams.json; run refresh --teams")
    return teams[key]


def _clock(text):
    match = re.fullmatch(r"(\d{1,2})(?::(\d{2}))? ([ap])\.m\.", text)
    if not match:
        raise ParseError(f"unrecognised time {text!r}")
    hour = int(match[1]) % 12 + (12 if match[3] == "p" else 0)
    return f"{hour:02d}:{match[2] or '00'}"


def find_gsoc_article(event_page, keyword):
    """Returns the path of an event's draw-schedule article, or None if not published yet."""
    pattern = r'/news/draw-schedule-set-for-[a-z0-9-]*' + re.escape(keyword) + r'[a-z0-9-]*'
    match = re.search(pattern, event_page)
    return match[0] if match else None


def parse_gsoc_article(page, event, teams):
    """Parses the draw table in a Grand Slam 'draw schedule set' article."""
    reader = _TableReader()
    reader.feed(page)
    draws, day = [], None
    for row in reader.rows:
        if len(row) == 1 or (row and not row[1:] or all(not c for c in row[1:])):
            match = re.fullmatch(r"[A-Z][a-z]+day, ([A-Z][a-z]{2})[a-z]*\.? (\d{1,2})", row[0])
            if match:
                month = SHORT_MONTHS[match[1]]
                day = date(_year_for(month, event["start"]), month, int(match[2]))
            continue
        if not re.match(r"\d{1,2}(:\d{2})? [ap]\.m\.", row[0]):
            continue
        if day is None:
            raise ParseError(f"{event['id']}: draw row before any day heading: {row}")
        number = row[1]
        local = f"{day.isoformat()}T{_clock(row[0])}"
        games, labels = [], []
        for cell in filter(None, row[2:]):
            sides = re.split(r"\s*vs\.\s*", cell)
            if len(sides) == 2:
                games.append({"a": sides[0], "b": sides[1],
                              "a_c": _gsoc_country(sides[0], event, teams),
                              "b_c": _gsoc_country(sides[1], event, teams)})
            elif PLAYOFF_WORDS.search(cell):
                labels.append(cell)
            else:
                raise ParseError(f"{event['id']}: draw {number} has an unrecognised cell {cell!r}")
        label = " / ".join([f"Draw {number}"] + labels)
        draw = _draw(event["id"], local, f"Draw {number}", games, bool(labels))
        draw["label"] = label
        draws.append(draw)
    if not draws:
        raise ParseError(f"{event['id']}: no draw table found in the article")
    return draws


def parse_rock_league(page, event):
    """Parses rockleague.com/schedule, keeping only games inside this event's dates."""
    draws = []
    for segment in page.split("c_matchitem-wrap")[1:]:
        when = re.search(r">(\d{2}) ([A-Z][a-z]+) (\d{4})<", segment)
        clock = re.search(r">(\d{1,2}):(\d{2}) ([ap])m<", segment)
        if not when or not clock or when[2] not in MONTHS:
            continue  # the championship game is listed with no date or time until it is set
        day = date(int(when[3]), MONTHS[when[2]], int(when[1]))
        if not event["start"] <= day <= event["end"]:
            continue
        hour = int(clock[1]) % 12 + (12 if clock[3] == "p" else 0)
        local = f"{day.isoformat()}T{hour:02d}:{clock[2]}"
        names = list(dict.fromkeys(re.findall(r'<img alt="([^"]+)"', segment)))
        names = [html_lib.unescape(name) for name in names]
        if len(names) >= 2:
            a, b = names[0], names[1]
            games = [{"a": a, "b": b, "a_c": FRANCHISE_COUNTRY.get(a, ""),
                      "b_c": FRANCHISE_COUNTRY.get(b, "")}]
            draws.append(_draw(event["id"], local, f"{a} v {b}", games, False))
            continue
        seeds = re.findall(r">\s*([1-6?])\s*<", segment)
        if len(seeds) != 2:
            raise ParseError(f"{event['id']}: game at {local} has neither teams nor seeds")
        label = "Final" if seeds == ["?", "?"] else f"Seed {seeds[0]} v Seed {seeds[1]}"
        draws.append(_draw(event["id"], local, label, [], True))
    return draws


def _check_stated_offset(text, event):
    """Fails if a Wikipedia article's stated UTC offset disagrees with the venue's time zone."""
    stated = re.search(r"draw times are listed in.*?UTC\s*([−+-])\s*(\d{1,2}):(\d{2})", text, re.I)
    if not stated:
        return  # no offset stated; times are taken as venue-local, as with World Curling
    sign = -1 if stated[1] in "−-" else 1
    minutes = sign * (int(stated[2]) * 60 + int(stated[3]))
    moment = datetime(event["start"].year, event["start"].month, event["start"].day, 12,
                      tzinfo=ZoneInfo(event["tz"]))
    expected = int(moment.utcoffset().total_seconds() / 60)
    if minutes != expected:
        raise ParseError(f"{event['id']}: article says UTC offset {minutes} min, venue is {expected} min")


def _wiki_team(value):
    """'{{NL}} (Young) {{X}}' becomes 'NL (Young)'. Blank or unfilled entries become 'TBD'."""
    code = re.search(r"\{\{([A-Z]{2,3})\}\}", value)
    skip = re.search(r"\(([^)]+)\)", value)
    if code and skip:
        return f"{code[1]} ({skip[1]})"
    plain = re.sub(r"\{\{[^}]*\}\}", "", value).strip()
    return plain or "TBD"


def parse_wikipedia(text, event):
    """Parses the draw sections of a Canadian championship article. Every team is Canadian."""
    _check_stated_offset(text, event)
    draws, heading, local, teams = [], None, None, []

    def close():
        nonlocal local, teams
        if local is None:
            return
        games = [{"a": a, "b": b, "a_c": "CAN", "b_c": "CAN"} for a, b in zip(teams[::2], teams[1::2])]
        draws.append(_draw(event["id"], local, heading, games, not heading.startswith("Draw")))
        local, teams = None, []

    started = False
    for line in text.split("\n"):
        title = re.fullmatch(r"=+\s*(.+?)\s*=+", line.strip())
        if title:
            close()
            started = started or title[1].lower().startswith("round robin results")
            heading = title[1]
            continue
        if not started:
            continue
        when = re.fullmatch(r"''[A-Z][a-z]+day, ([A-Z][a-z]+) (\d{1,2}), (\d{1,2}):(\d{2}) ([ap])m''", line.strip())
        if when and when[1] in MONTHS:
            close()
            month = MONTHS[when[1]]
            day = date(_year_for(month, event["start"]), month, int(when[2]))
            hour = int(when[3]) % 12 + (12 if when[5] == "p" else 0)
            local = f"{day.isoformat()}T{hour:02d}:{when[4]}"
            continue
        team = re.match(r"\|\s*team[12]\s*=(.*)", line)
        if team and local is not None:
            teams.append(_wiki_team(team[1]))
    close()
    return draws


def hometown_country(hometown):
    """Maps a Grand Slam 'Hometown' string to a country code, or "" for anywhere untracked."""
    if re.search(rf",\s*({PROVINCES})\s*$", hometown):
        return "CAN"
    if re.search(r"Spain|España", hometown):
        return "ESP"
    if re.search(r"Turkey|Türkiye", hometown):
        return "TUR"
    if re.search(r"Czechia|Czech Republic", hometown):
        return "CZE"
    return ""
