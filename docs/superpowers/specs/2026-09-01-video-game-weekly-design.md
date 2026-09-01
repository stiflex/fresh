# fresh — video-game weekly digest — design

A weekly digest of what happened in video games, forked from the `tentac`
project's `ai-weekly` pipeline (`D:\Projets\tentac`). Same architecture,
retargeted content domain, different visual design. Static site, no backend,
deployed from the repo to GitHub Pages under `github.com/stiflex`.

## What's reused verbatim from tentac

The pipeline, scoring mechanics, and site mechanics carry over unchanged —
they're already validated. Only the content domain (sources/sections) and the
visual design differ. Reused as-is:

- **`pipeline/models.py`** — `RawItem`, `dedupe_key` (DOI > arXiv-equivalent ID
  > CVE-equivalent > normalized URL), `SectionId` literal (swapped to the new
  section list below).
- **`pipeline/dates.py`** — ISO week bounds, UTC, half-open `[monday, next
  monday)`.
- **`pipeline/fetch.py`** — per-source try/except (one dead feed never fails
  the run), `tenacity` retry (3 attempts, exponential backoff), ETag/
  Last-Modified caching under `.cache/`, one `SELECTORS` dict for any scraped
  sources. No arXiv-specific rate limiting needed (no arXiv sources here).
- **`pipeline/score.py`** — dedupe (exact `dedupe_key` + `rapidfuzz` near-dupe
  title match ≥ 92), prefilter before spending tokens, batched Claude
  classify+score call (`{"url","section","score","why"}`, validated against
  `SectionId`, malformed rows dropped), rank formula
  `0.55*model_score + 0.25*social + 0.20*source_tier`, cap 6 items/section.
- **`pipeline/build.py`** — atomic issue write (temp file + rename).
- **Astro route layout** — `/` (latest), `/w/[week]` (past issue), `/archive`
  (all issues, newest first), `/rss.xml`. Zero client JS by default, reads
  `data/*.json` at build time via `import.meta.glob`.
- **Automation** — `weekly.yml` (Monday 06:00 UTC cron: fetch + prefilter,
  opens a PR on `issue/<week>` with `data/raw/<week>.jsonl` and
  `data/prefiltered/<week>.jsonl`, no secrets needed) and `deploy.yml`
  (builds + publishes on merge to `main`). No `ANTHROPIC_API_KEY` in CI.
- **Weekly workflow** — classification runs locally via a gitignored
  `.claude/skills/weekly-analysis` Claude Code skill (adapted prompt, see
  below), reviewed as a normal PR diff before merge — same no-API-key,
  human-in-the-loop pattern as tentac.
- **Quality floor** — responsive to mobile, visible keyboard focus,
  `prefers-reduced-motion` respected.

## What changes

### Sections (7, down from tentac's 8 — no security section)

| id | label | blurb |
|---|---|---|
| `releases` | Releases & updates | New launches, major patches, DLC |
| `indie` | Indie spotlight | Indie & showcase highlights |
| `industry` | Industry | Funding, acquisitions, layoffs, publisher news |
| `esports` | Esports | Competitive scene, tournaments |
| `hardware` | Hardware | Consoles, GPUs, peripherals, storefront tech |
| `community-modding` | Community & modding | Mods, UGC, speedrunning |
| `dev-tech` | Dev & tech | Engines, tools, postmortems, technical breakdowns |

A section with no items renders as a quiet week — never padded.

### Sources (`config/sources.yaml`)

Same shape as tentac's registry (`kind: rss | api | scrape`, `tier: 1-3`,
`sections:` as a prior). Starting set, tiered by signal quality:

- **Tier 1:** IGN, Kotaku, Polygon, Rock Paper Shotgun, Steam news/updates
  feed, itch.io new-and-popular.
- **Tier 2:** PC Gamer, Eurogamer, GameSpot, VG247, Game Developer (formerly
  Gamasutra), Dot Esports, Dexerto.
- **Tier 3:** Ars Technica Gaming, r/Games and r/gamedev (via RSS).

No arXiv-equivalent academic tier exists for this domain — drop that source
type entirely rather than force-fit it.

### Visual design — minimal newsletter, not tentac's look

Explicitly **not** a copy of tentac's site design. Single column, section
headings with a short plain list underneath — no cards, no thumbnails/images,
no grid. Generous whitespace, one accent color. One deliberately picked
display+body font pairing (chosen during the Phase-3 design pass, not
tentac's fonts) — still minimal, just enough to avoid reading as an unstyled
default. Each item's `why` line is the primary text, title secondary, same as
tentac. Dark mode: not assumed — decide during the Phase-3 design pass.

### Branding

Public-facing name: **fresh** (page titles, headings, RSS feed, README). No
internal/public name split is needed since the package starts fresh — call
the pipeline package `fresh` too.

### Repo / hosting

New repo created under `github.com/stiflex` (created empty by the user
outside this session; this session sets the `origin` remote and pushes once
it exists). GitHub Pages enabled the same way as tentac (Settings → Pages →
source: GitHub Actions).

## Local skill: `weekly-analysis`

Same role as tentac's: local, no-API-key classification step run via Claude
Code (`.claude/skills/weekly-analysis/`, gitignored — internal tooling, not
part of the public repo). Prompt adapted for the video-game domain and the
7-section list above; otherwise same responsibilities — classify + score
every prefiltered item, verify each source link resolves (drop dead links),
rank, write the headline, assemble `data/<week>.json`.

## Out of scope for this spec

- Email digest, per-section RSS, a `notable`-incident pin — same as tentac's
  Phase 5, worth having later, not part of the initial build.
- Fully-automated CI classification (an `ANTHROPIC_API_KEY` secret path) —
  the code may end up supporting it the way tentac's does, but the default
  workflow is the local-skill path only.
