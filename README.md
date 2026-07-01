# status-line-cost

A Claude Code status line that renders `model | in tokens $ | out tokens $ | total $`,
with the cost computed **locally** from the token counts already sitting in the
session transcript — no provider API call, no surprise network hop while you're
mid-thought.

## What it shows

On every status-line refresh Claude Code pipes a JSON blob (`session_id`,
`transcript_path`, `model`, `cwd`) to `scripts/claude_cost.py` on stdin. The
script tallies every `message.usage` block in the transcript, applies the
per-model rates (base input, cache-write 1.25x/2x, cache-read 0.1x, output), and
prints a single colored line.

## Prerequisites

- **A way to run Python 3 — either `python3` on your `PATH`, or `uvx`.** The
  renderer is **stdlib-only** (`json`, `os`, `sys`, `pathlib`, `datetime` —
  pricing is plain JSON), so there are no dependencies to install either way.
  - **`python3`** — any reasonably modern CPython runs it directly. No uv, no
    virtualenv, no `pip install`.
  - **`uvx`** — if you manage Python through [uv](https://docs.astral.sh/uv/) and
    don't keep a system `python3` around, `uvx python@3 <script>` fetches a managed
    interpreter on demand and runs it (see [wire-up](#wire-up-the-status-line) for
    the exact command). Still zero dependencies — uv just supplies the interpreter.

## Install

Enabling the plugin ships the renderer script; you then wire the status line into
your own `settings.json` (see [below](#wire-up-the-status-line)). Pick the install
mode that fits.

### From GitHub (marketplace)

This repo doubles as a plugin marketplace. Add it and install:

```
/plugin marketplace add il/status-line-cost
/plugin install cost-statusline@status-line-cost
```

A marketplace-installed plugin lands in Claude Code's plugin cache, so reference
its bundled script through **`${CLAUDE_PLUGIN_ROOT}`** — the plugin's own install
directory — never a hardcoded cache path (that path changes on every update).

### Wire up the status line

A plugin **cannot** ship the main `statusLine` — Claude Code applies only the
`agent` and `subagentStatusLine` keys from a plugin's bundled `settings.json`, so
the status-line command has to live in *your* settings. Add this to the
appropriate `settings.json`:

```json
// user settings (~/.claude/settings.json)
"statusLine": {
  "type": "command",
  "command": "python3 \"${CLAUDE_PLUGIN_ROOT}/scripts/claude_cost.py\""
}
```

`${CLAUDE_PLUGIN_ROOT}` resolves to the plugin's install dir — a documented
`statusLine` substitution, so the path stays correct across updates. The status
line lights up on the next session.

No system `python3`? Swap the leading `python3` for `uvx python@3` — uv fetches a
managed interpreter and runs the same script (the renderer needs no packages):

```json
"statusLine": {
  "type": "command",
  "command": "uvx python@3 \"${CLAUDE_PLUGIN_ROOT}/scripts/claude_cost.py\""
}
```

## Pricing

Rates live in `llm_price_tag.json`, matched by substring against the session model
id with a `default` fallback (the `default` entry sits inside `models`). Every
entry has the **same shape** — a `rates` list of snapshots, newest last, each
carrying the base per-token rates *and* the cache multipliers together:

```json
"claude-opus-4-8": {
  "rates": [{
    "effective_date": "2026-07-01",
    "input": 5.0, "output": 25.0,
    "cache_write_5m": 1.25, "cache_write_1h": 2.0, "cache_read": 0.1
  }]
}
```

`input`/`output` are first-party Claude API list prices, per Anthropic's published
pricing ([§ Model pricing](https://platform.claude.com/docs/en/about-claude/pricing)).

### Rate schedules

Because the rate is a list, a model that reprices on a known date carries both
rates and switches on its own — the renderer picks the snapshot whose
`effective_date` is the latest one not in the future. Sonnet 5 is the live case:
introductory `$2`/`$10` through 2026-08-31, then standard `$3`/`$15` from
September 1, with no edit when the promo lapses.

```json
"claude-sonnet-5": {
  "rates": [
    {"effective_date": "2026-06-01", "input": 2.0, "output": 10.0, "cache_write_5m": 1.25, "cache_write_1h": 2.0, "cache_read": 0.1},
    {"effective_date": "2026-09-01", "input": 3.0, "output": 15.0, "cache_write_5m": 1.25, "cache_write_1h": 2.0, "cache_read": 0.1}
  ]
}
```

Stable models keep a one-element list. The `effective_date` doubles as a freshness
marker: set `$DBDOCS_STATUSLINE_SHOW_DATE` to append `rates <date>` (the active
model's resolved date) to the status line.

### Cache multipliers

`cache_write_5m`, `cache_write_1h`, and `cache_read` are multipliers on the
snapshot's base `input` rate, per Anthropic's published prompt-caching pricing —
5-minute cache write **1.25×**, 1-hour cache write **2×**, cache read (hits &
refreshes) **0.1×**
([§ Pricing](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)).
They live in each rate snapshot alongside `input`/`output`; nothing is hardcoded
in the renderer, so the JSON is the single source of truth.

### Overriding rates per project

Pricing loads in **layers**, each merging over the previous per model — so a
project overrides only what it cares about and inherits the rest:

1. **Bundled defaults** — this plugin's `llm_price_tag.json`. Always the base.
2. **Project-local** — `$CLAUDE_PROJECT_DIR/.claude/llm_price_tag.json` (falls back
   to `./.claude/llm_price_tag.json` when the env var is unset). Commit this to
   pin a team's negotiated rates.
3. **Explicit path** — `$DBDOCS_STATUSLINE_PRICING=/path/to/rates.json`. Wins over
   both; handy for a one-off experiment without touching committed files.

A project file only needs the entries it changes — to retag Opus and nudge the
unknown-model fallback:

```json
{
  "models": {
    "claude-opus-4-8": {
      "rates": [{"effective_date": "2026-07-01", "input": 4.5, "output": 22.5, "cache_write_5m": 1.25, "cache_write_1h": 2.0, "cache_read": 0.1}]
    },
    "default": {
      "rates": [{"effective_date": "2026-07-01", "input": 6.0, "output": 30.0, "cache_write_5m": 1.25, "cache_write_1h": 2.0, "cache_read": 0.1}]
    }
  }
}
```

Every model you *don't* list keeps its bundled rate. Missing or malformed override
files are ignored (the bundled defaults still load), so a typo degrades gracefully
instead of blanking the status line.
