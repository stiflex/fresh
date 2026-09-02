# fresh

**Live site: https://stiflex.github.io/fresh/**

A weekly digest of what happened in video games — releases, industry news,
esports, hardware, community/modding, and dev/tech — picked and summarized
by Claude. No `ANTHROPIC_API_KEY` anywhere: fetching is automated, the
classification step is done locally via Claude Code, and the result is
reviewed as a normal pull request before it goes live.

Forked from the architecture of [tentac](https://baiddd.github.io/tentac/),
a sibling AI-news digest, and retargeted to games.

- `config/sources.yaml` — every source, grouped by section, with a tier
- `pipeline/` — fetch → score (dedupe/prefilter/classify/rank) → build →
  upcoming (the cross-week games calendar)
- `data/` — one committed JSON file per week; the archive is the git
  history. `data/upcoming.json` tracks every game with a stated release
  date, once each, until that date passes.
- `web/` — Astro site published to GitHub Pages. Minimal newsletter
  design — single column, one accent color, no cards or images. Each item
  shows its editorial `why` line, a cleaned excerpt of the source's own
  summary, and — when another outlet covered the same story — an "Also
  covered by" link to it.

## Sections

Seven, defined in `config/sources.yaml`:

- **Games of the week** — games, expansions, and major updates that
  actually shipped this week.
- **Releases & updates** — announcements, previews, delays, and other news
  about upcoming games (distinct from the section above: this is talk
  *about* a release, not the release itself).
- **Indie spotlight**, **Industry**, **Hardware**, **Community & modding**,
  **Dev & tech** — as named.

A section with no items renders as a quiet week — never padded to fill it.

## Duplicate stories across outlets

`score.py`'s `dedupe()` only catches near-identical titles or identical
URLs — it won't catch two outlets covering the same story in unrelated
wording (e.g. "Xbox announces a disc-to-digital feature" vs. "Xbox's new
disc-to-digital program..."). Recognizing that takes judgment, so it's the
classification step's job: `classify_and_score` (and the equivalent manual
classification pass) can mark an item `duplicate_of` another item's URL
*within the same batch*; the duplicate is folded into the canonical item's
`mirrors` instead of shipping as its own digest entry, and shown on the
site as "Also covered by". A `duplicate_of` pointing outside the batch
(cross-batch, or hallucinated) is ignored, not treated as a drop.

## The upcoming-games calendar

Any item whose article states both a game name and a release date is a
candidate for `data/upcoming.json`, a running list of games with their
stated release date, platforms (when named), and source — deduped by
normalized game name, first source wins, and dropped once the date has
passed. Shown at `/upcoming` on the site.

**Ordering matters:** `pipeline/upcoming.py` must run against the *full*
classified `data/scored/<week>.jsonl`, before `score.py --stage rank`
trims that file down to each section's top items — a game whose article
didn't make that cut still belongs on the calendar. See both files'
docstrings.

## How a weekly issue happens

1. **Automatic (GitHub Actions, every Monday ~06:18 UTC, no secrets
   needed):** `weekly.yml` runs `pipeline/fetch.py` then
   `pipeline/score.py --stage prefilter` (dedupe + prefilter — pure code,
   no LLM), and opens a PR on a branch named `issue/<week>` with
   `data/raw/<week>.jsonl` and `data/prefiltered/<week>.jsonl`.
2. **Local, via Claude Code (no API key):**
   ```bash
   git fetch && git checkout issue/2026-W36
   ```
   Open Claude Code in the repo and classify every item in
   `data/prefiltered/<week>.jsonl` into `data/scored/<week>.jsonl` —
   section, score, a ≤20-word `why` line, `game_name`/`release_date`/
   `platforms` whenever the article itself states them (never inferred),
   and `duplicate_of` when another item covers the same story (see
   "Duplicate stories across outlets" below). Then, **in this order**:
   ```bash
   python pipeline/upcoming.py --week 2026-W36   # before rank — see above
   python pipeline/score.py --week 2026-W36 --stage rank
   python pipeline/build.py --week 2026-W36 \
     --headline "..." --title "..." --analyzed-by claude-sonnet-5
   ```
   Review the diff, commit, and push to the same branch.
3. **Merge the PR.** Merging to `main` triggers `deploy.yml`, which builds
   the Astro site and publishes it to GitHub Pages automatically — no
   manual deploy step.

## Local run (manual, for testing a single stage)

```bash
python -m venv .venv && .venv/Scripts/pip install -r requirements-dev.txt   # or .venv/bin/pip on macOS/Linux
.venv/Scripts/python pipeline/fetch.py --week 2026-W36        # or --date 2026-09-01
.venv/Scripts/python pipeline/score.py --week 2026-W36 --stage prefilter
# classify data/prefiltered/2026-W36.jsonl -> data/scored/2026-W36.jsonl by hand or via Claude Code, then:
.venv/Scripts/python pipeline/upcoming.py --week 2026-W36
.venv/Scripts/python pipeline/score.py --week 2026-W36 --stage rank
.venv/Scripts/python pipeline/build.py --week 2026-W36 --headline "..." --title "..."
```

`classify_and_score()` (in `pipeline/score.py`) and `write_headline()`/
`write_title()` (in `pipeline/build.py`) still exist and are fully
unit-tested — they call the `anthropic` SDK directly and need a real
`ANTHROPIC_API_KEY`. Nothing in this repo's default workflow calls them;
they're there for anyone who later wants to fully automate the
classification step in CI instead of doing it locally via Claude Code.

Run the test suite with:

```bash
.venv/Scripts/pip install -r requirements-dev.txt
.venv/Scripts/python -m pytest
```

For the site:

```bash
cd web && npm install && npm run dev
```

## Setup (one-time)

1. Enable Pages: Settings → Pages → source **GitHub Actions**.
2. Trigger `Fetch weekly issue` manually once
   (`gh workflow run weekly.yml`) to confirm the PR-opening flow works,
   before relying on the Monday cron.
