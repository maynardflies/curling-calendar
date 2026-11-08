import unittest
from datetime import date

from curlcal import build, parsers

EVENT = {"id": "t", "start": date(2027, 1, 4), "end": date(2027, 1, 10), "tz": "Europe/Helsinki",
         "name": "Test", "city": "X", "watch": "curling_channel", "game_hours": 2.0,
         "tier": 1, "url": "u", "source": {"type": "wcf"}}
WCF = """<h2>Schedule</h2><div>4 January</div><div>–</div><div>16:30</div>
<div>CAN</div><div>NZL</div><div>FRA</div><div>FIN</div>
<div>9 January</div><div>19:00 (Qualification)</div><div>TBC</div><div>TBC</div>
<div>Historic Results</div>"""


class ParserTests(unittest.TestCase):
    def test_wcf_pairs_teams_and_marks_playoffs(self):
        draws = parsers.parse_wcf(WCF, EVENT)
        self.assertEqual(len(draws), 2)
        self.assertEqual(draws[0]["local"], "2027-01-04T16:30")
        self.assertEqual([(g["a"], g["b"]) for g in draws[0]["games"]], [("CAN", "NZL"), ("FRA", "FIN")])
        self.assertTrue(draws[1]["playoff"])

    def test_wcf_odd_team_count_fails_loudly(self):
        page = "<h2>Schedule</h2><div>4 January</div><div>16:30</div><div>CAN</div><div>Historic</div>"
        with self.assertRaises(parsers.ParseError):
            parsers.parse_wcf(page, EVENT)

    def test_wcf_unknown_line_fails_loudly(self):
        page = "<h2>Schedule</h2><div>4 January</div><div>16:30</div><div>Canada vs NZ</div>"
        with self.assertRaises(parsers.ParseError):
            parsers.parse_wcf(page, EVENT)

    def test_hometown_country(self):
        self.assertEqual(parsers.hometown_country("Winnipeg, MB"), "CAN")
        self.assertEqual(parsers.hometown_country("Chaska, MN"), "")
        self.assertEqual(parsers.hometown_country("Erzurum, Turkey"), "TUR")
        self.assertEqual(parsers.hometown_country("Prague, Czechia"), "CZE")
        self.assertEqual(parsers.hometown_country("Jaca, Spain"), "ESP")

    def test_gsoc_unknown_team_fails_loudly(self):
        page = ("<table><tr><td>Tuesday, Nov. 17</td></tr><tr><td>12 p.m.</td><td>2</td>"
                "<td>Nobody vs. Homan</td></tr></table>")
        event = dict(EVENT, start=date(2026, 11, 17))
        with self.assertRaises(parsers.ParseError):
            parsers.parse_gsoc_article(page, event, {"homan": "CAN"})

    def test_gsoc_unrecognised_cell_fails_loudly(self):
        page = ("<table><tr><td>Tuesday, Nov. 17</td></tr><tr><td>12 p.m.</td><td>2</td>"
                "<td>Homan against Sturmay</td></tr></table>")
        event = dict(EVENT, start=date(2026, 11, 17))
        with self.assertRaises(parsers.ParseError):
            parsers.parse_gsoc_article(page, event, {"homan": "CAN"})

    def test_gsoc_matchup_with_no_spaces_around_vs(self):
        page = ("<table><tr><td>Tuesday, Nov. 17</td></tr><tr><td>12 p.m.</td><td>2</td>"
                "<td><span>Homan</span><span>vs.</span><span>Sturmay</span></td></tr></table>")
        event = dict(EVENT, start=date(2026, 11, 17))
        draw = parsers.parse_gsoc_article(page, event, {"homan": "CAN", "sturmay": "CAN"})[0]
        self.assertEqual((draw["games"][0]["a"], draw["games"][0]["b"]), ("Homan", "Sturmay"))
        self.assertFalse(draw["playoff"])

    def test_gsoc_accents_match_site_spelling_and_placeholder_is_allowed(self):
        page = ("<table><tr><td>Tuesday, Nov. 17</td></tr><tr><td>12 p.m.</td><td>2</td>"
                "<td>Pätz vs. Halifax Qualifier</td></tr></table>")
        event = dict(EVENT, start=date(2026, 11, 17))
        draw = parsers.parse_gsoc_article(page, event, {"paetz": ""})[0]
        self.assertEqual(draw["local"], "2026-11-17T12:00")
        self.assertEqual(draw["label"], "Draw 2")


WIKI = """==Round robin results==
All draw times are listed in [[Atlantic Time]] ([[UTC−04:00]]).
===Draw 1===
''Friday, February 12, 7:30 pm''
{{Curlingbox
| sheet = A
| team1 = {{NS}} (Black) {{X}}
|0|1| |8
| team2 = {{MB}} (Einarson)
|1|0| |4
}}
==Playoffs==
===Final===
''Sunday, February 21, 1:30 pm''
"""


class WikipediaTests(unittest.TestCase):
    event = dict(EVENT, id="s", start=date(2027, 2, 12), end=date(2027, 2, 21), tz="America/Halifax")

    def test_draws_and_playoffs(self):
        draws = parsers.parse_wikipedia(WIKI, self.event)
        self.assertEqual(draws[0]["local"], "2027-02-12T19:30")
        self.assertEqual((draws[0]["games"][0]["a"], draws[0]["games"][0]["b"]), ("NS (Black)", "MB (Einarson)"))
        self.assertFalse(draws[0]["playoff"])
        self.assertEqual((draws[1]["label"], draws[1]["games"], draws[1]["playoff"]), ("Final", [], True))

    def test_wrong_time_zone_fails_loudly(self):
        with self.assertRaises(parsers.ParseError):
            parsers.parse_wikipedia(WIKI, dict(self.event, tz="America/Regina"))


class BuildTests(unittest.TestCase):
    def test_venue_time_converts_across_dst_gap(self):
        # Canada has changed to summer time on Mar 21 but Europe has not.
        event = dict(EVENT, tz="America/Moncton", start=date(2027, 3, 21))
        draw = {"id": "d", "local": "2027-03-21T14:00", "label": "", "playoff": False,
                "games": [{"a": "CAN", "b": "SWE", "a_c": "CAN", "b_c": "SWE"}]}
        entry = build.game_entries(event, [draw], ["CAN"])[0]
        self.assertEqual(entry["start"], "2027-03-21T17:00:00Z")

    def test_untracked_draws_are_dropped(self):
        draw = {"id": "d", "local": "2027-01-04T14:00", "label": "", "playoff": False,
                "games": [{"a": "FRA", "b": "FIN", "a_c": "FRA", "b_c": "FIN"}]}
        self.assertEqual(build.game_entries(EVENT, [draw], ["CAN"]), [])

    def test_folding_respects_octets_and_multibyte_characters(self):
        folded = build._fold("SUMMARY:" + "🇨🇦" * 40)
        for line in folded.split("\r\n"):
            self.assertLessEqual(len(line.encode("utf-8")), 75)
        self.assertEqual(folded.replace("\r\n ", ""), "SUMMARY:" + "🇨🇦" * 40)

    def test_escaping(self):
        self.assertEqual(build._escape("a,b;c\nd"), "a\\,b\\;c\\nd")


if __name__ == "__main__":
    unittest.main()
