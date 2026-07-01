#!/usr/bin/env python3
"""Status-line renderer for Claude Code: model | tokens | session cost.

Claude Code invokes this on every status-line refresh, passing a JSON object on
stdin that includes at least ``session_id``, ``transcript_path``, ``model``, and
``cwd``. Cost is computed *locally* from the token counts already recorded in the
session transcript (one JSON object per line, each carrying a ``message.usage``
block) — no provider API call is made. Per-million-token ``input``/``output``
rates in ``llm_price_tag.json`` are first-party Claude API list prices, per
Anthropic's published pricing:
https://platform.claude.com/docs/en/about-claude/pricing (§ Model pricing).

Every entry under ``models`` — including the ``default`` fallback — has the same
shape: a ``rates`` list of ``{effective_date, input, output, cache_write_5m,
cache_write_1h, cache_read}`` snapshots, newest last. Base per-token rates and
cache-token multipliers live together in each snapshot. The renderer picks the
snapshot in effect today, so a model that reprices on a known date carries both
rates and switches on its own — e.g. Sonnet 5's introductory $2/$10 through
2026-08-31, then the standard $3/$15 from 2026-09-01. Stable models simply have a
one-element list. ``effective_date`` is otherwise informational: the active
model's is appended to the status line when ``$DBDOCS_STATUSLINE_SHOW_DATE`` is
set, so a stale rate is visible at a glance.

Cache tokens are priced as multiples of the base input rate — cache-write is
1.25x for the 5-minute TTL (``cache_write_5m``) and 2x for the 1-hour TTL
(``cache_write_1h``), cache-read (``cache_read``) 0.1x, per
https://platform.claude.com/docs/en/build-with-claude/prompt-caching (§ Pricing).
Nothing is hardcoded in this script, so the JSON is the single source of truth.

Pricing loads in layers, each merging over the previous per-model so a project
only overrides what it wants:

1. the plugin's bundled ``llm_price_tag.json`` (defaults) — always the base;
2. the project file at ``$CLAUDE_PROJECT_DIR/.claude/llm_price_tag.json`` (or
   ``./.claude/llm_price_tag.json`` when that env var is unset);
3. an explicit path in ``$DBDOCS_STATUSLINE_PRICING`` (wins over both).

Pricing is plain JSON so the status line runs on any stock Python 3 — Claude
Code invokes the command with the login-shell ``python3``, which is not
guaranteed to have third-party packages installed.
"""

import datetime
import json
import os
import sys
from pathlib import Path

BUNDLED_PRICING_PATH = Path(__file__).resolve().parent.parent / "llm_price_tag.json"

RESET = "\033[0m"
DIM = "\033[2m"
CYAN = "\033[36m"
GREEN = "\033[32m"


def _pricing_paths():
    paths = [BUNDLED_PRICING_PATH]
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR")
    base = Path(project_dir) if project_dir else Path.cwd()
    paths.append(base / ".claude" / "llm_price_tag.json")
    override = os.environ.get("DBDOCS_STATUSLINE_PRICING")
    if override:
        paths.append(Path(override))
    return paths


def _load_pricing():
    models = {}
    for path in _pricing_paths():
        try:
            layer = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            continue
        if not isinstance(layer, dict):
            continue
        models.update(layer.get("models") or {})
    return models


PRICING = _load_pricing()


def _resolve_schedule(entry):
    """Resolve a model entry's ``rates`` list to the snapshot in effect today.

    Every entry is ``{rates: [{effective_date, input, output, cache_write_5m,
    cache_write_1h, cache_read}, ...]}`` — base rates and cache multipliers live
    together per snapshot. Pick the latest snapshot whose ``effective_date`` is
    not in the future; if today precedes every snapshot, fall back to the earliest
    so a rate is always returned. A dict without ``rates`` (e.g. a flat override)
    is passed through unchanged.
    """
    if not isinstance(entry, dict):
        return entry
    rates = entry.get("rates")
    if not rates:
        return entry
    today = datetime.date.today().isoformat()
    ordered = sorted(rates, key=lambda r: r.get("effective_date", ""))
    current = ordered[0]
    for entry in ordered:
        if entry.get("effective_date", "") <= today:
            current = entry
        else:
            break
    return current


def _price_for(model):
    if model:
        for key, entry in PRICING.items():
            if key != "default" and key in model:
                return _resolve_schedule(entry)
    return _resolve_schedule(PRICING.get("default") or {})


def _cost_for_usage(usage, price):
    input_tokens = usage.get("input_tokens", 0) or 0
    output_tokens = usage.get("output_tokens", 0) or 0
    cache_read = usage.get("cache_read_input_tokens", 0) or 0
    creation = usage.get("cache_creation") or {}
    write_5m = creation.get("ephemeral_5m_input_tokens")
    write_1h = creation.get("ephemeral_1h_input_tokens")
    if write_5m is None and write_1h is None:
        write_5m = usage.get("cache_creation_input_tokens", 0) or 0
        write_1h = 0
    write_5m = write_5m or 0
    write_1h = write_1h or 0

    per_million = (price.get("input") or 0.0) / 1_000_000
    out_per_million = (price.get("output") or 0.0) / 1_000_000
    input_cost = (
        input_tokens * per_million
        + cache_read * per_million * (price.get("cache_read") or 0.0)
        + write_5m * per_million * (price.get("cache_write_5m") or 0.0)
        + write_1h * per_million * (price.get("cache_write_1h") or 0.0)
    )
    output_cost = output_tokens * out_per_million
    input_tokens_billable = input_tokens + cache_read + write_5m + write_1h
    return input_cost, output_cost, input_tokens_billable, output_tokens


def _tally(transcript_path, fallback_model):
    totals = {"in_cost": 0.0, "out_cost": 0.0, "in_tokens": 0, "out_tokens": 0}
    seen_model = fallback_model
    try:
        text = Path(transcript_path).read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return totals, seen_model
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        message = record.get("message") or {}
        usage = message.get("usage")
        if not usage:
            continue
        model = message.get("model") or seen_model
        seen_model = model
        in_cost, out_cost, in_tokens, out_tokens = _cost_for_usage(
            usage, _price_for(model)
        )
        totals["in_cost"] += in_cost
        totals["out_cost"] += out_cost
        totals["in_tokens"] += in_tokens
        totals["out_tokens"] += out_tokens
    return totals, seen_model


def _humanize_tokens(tokens):
    if tokens >= 1_000_000:
        return f"{tokens / 1_000_000:.1f}M"
    if tokens >= 1_000:
        return f"{tokens / 1_000:.1f}k"
    return str(tokens)


def _short_model(model):
    if not model:
        return "?"
    return model.replace("claude-", "").replace("[1m]", "")


def main():
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        payload = {}

    raw_model = payload.get("model")
    model = raw_model.get("id") if isinstance(raw_model, dict) else raw_model
    transcript_path = payload.get("transcript_path", "")

    totals, model = _tally(transcript_path, model)
    in_cost = totals["in_cost"]
    out_cost = totals["out_cost"]
    total_cost = in_cost + out_cost

    parts = [
        f"{CYAN}{_short_model(model)}{RESET}",
        f"{DIM}in {_humanize_tokens(totals['in_tokens'])} ${in_cost:.2f}{RESET}",
        f"{DIM}out {_humanize_tokens(totals['out_tokens'])} ${out_cost:.2f}{RESET}",
        f"{GREEN}${total_cost:.2f}{RESET}",
    ]
    if os.environ.get("DBDOCS_STATUSLINE_SHOW_DATE"):
        effective_date = _price_for(model).get("effective_date")
        if effective_date:
            parts.append(f"{DIM}rates {effective_date}{RESET}")
    sys.stdout.write(f" {DIM}|{RESET} ".join(parts))


if __name__ == "__main__":
    main()
