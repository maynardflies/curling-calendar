# Curling on TV

A subscribable calendar of the curling season: every tournament, plus the games involving
Canada, Türkiye and Czechia.

- `data/season.toml` lists the events by hand. Add or correct tournaments here.
- `python -m curlcal.refresh` reads the official schedules into `data/draws/`.
- `python -m curlcal.build` writes the page and feeds to `public/`.
- `python -m unittest discover -s tests -t .` runs the tests.

A GitHub Action runs this daily and publishes to GitHub Pages. The run fails if a source's layout
changes, or if World Curling lists an event that isn't in `season.toml`.

Feeds: `all.ics`, `tournaments.ics` and `games.ics`. To track another country, add its
three-letter code to `tracked` in `season.toml`, and a flag to `FLAGS` in `curlcal/build.py`.
