# status-line-cost

A Claude Code status line that renders `model | context % | $cost | duration | lines`,
reading every value straight from the JSON payload Claude Code pipes to it on each
refresh — no transcript parsing, no rate table, no provider API call. The session
cost is the one Claude Code already computes client-side (`cost.total_cost_usd`),
so the script just formats it rather than re-deriving it.

## What it shows

On every status-line refresh Claude Code pipes a JSON blob to
`scripts/claude_cost.sh` on stdin, and the script prints two colored lines:

![alt text](docs/assets/img/status-line-demo.png)

Each segment is pulled directly from the payload (the branch has one exception):

| Segment            | Source field                                          |
| ------------------ | ----------------------------------------------------- |
| model              | `.model.display_name` (falls back to `.model.id`)     |
| `73% ctx`          | `.context_window.used_percentage`                     |
| `$1.23`            | `.cost.total_cost_usd`                                |
| `3m 5s`            | `.cost.total_duration_ms`                             |
| `+156 -23`         | `.cost.total_lines_added` / `.cost.total_lines_removed` (hidden when both are 0) |
| 📁 folder          | leaf of `.workspace.current_dir`                      |
| 🌿 branch          | `.worktree.branch` / `.workspace.git_worktree` if present, else `git branch --show-current` in the folder (omitted outside a repo) |

The context percentage is color-coded: green under 70%, yellow 70–89%, red at 90%+
— so you notice you're running low on room before Claude does. The branch prefers
what Claude Code already reports for a worktree session and only shells out to
`git` when the payload doesn't carry one, so a `--worktree` session shows *its*
branch, not the main checkout's.

## Prerequisites

- **Bash** (any modern version; ships on macOS and Linux).
- **`jq`** for JSON parsing — the supported path. If `jq` isn't on your `PATH` the
  script quietly falls back to a `grep`/`cut` parser so the status line still
  renders; it's just less thorough. Install `jq` if you want the full experience
  (`brew install jq`, `apt install jq`, etc.).

There's nothing to `pip install` and no interpreter to manage — it's a shell script.

## Install

Enabling the plugin ships the renderer script; you then wire the status line into
your own `settings.json` (see [below](#wire-up-the-status-line)). Pick the install
mode that fits.

### From GitHub (marketplace)

This repo doubles as a plugin marketplace. Add it and install:

```
/plugin marketplace add il-dat/status-line-cost
/plugin install cost-statusline@status-line-cost
```

A marketplace-installed plugin lands in Claude Code's plugin cache, so reference
its bundled script through **`${CLAUDE_PLUGIN_ROOT}`** — the plugin's own install
directory — never a hardcoded cache path (that path changes on every update).

### Alternative: declarative install (settings.json)

For scripted, containerized, or team-provisioned setups where you can't run
interactive slash commands, register the marketplace and enable the plugin
directly in `settings.json`. This is the config-file equivalent of the two
commands above — `extraKnownMarketplaces` mirrors `/plugin marketplace add`, and
`enabledPlugins` mirrors `/plugin install`. You need **both**: registering the
marketplace alone won't turn the plugin on.

```json
{
  "extraKnownMarketplaces": {
    "status-line-cost": {
      "source": {
        "source": "github",
        "repo": "il-dat/status-line-cost",
        "ref": "1.0.0"
      }
    }
  },
  "enabledPlugins": {
    "cost-statusline@status-line-cost": true
  }
}
```

The `ref` pins the marketplace to the `1.0.0` tag, so you opt into new releases
deliberately rather than tracking `main`. Drop it to follow the default branch.
Note that a **marketplace** source takes `ref` (branch or tag) only — commit-SHA
pinning is a *plugin*-source feature, and here the plugin ships from this same
repo, versioned through `plugin.json`'s `"version": "1.0.0"`.

### Wire up the status line

A plugin **cannot** ship the main `statusLine` — Claude Code applies only the
`agent` and `subagentStatusLine` keys from a plugin's bundled `settings.json`, so
the status-line command has to live in *your* settings. Add this to the
appropriate `settings.json`:

```json
// user settings (~/.claude/settings.json)
"statusLine": {
  "type": "command",
  "command": "\"$HOME/.claude/plugins/marketplaces/status-line-cost/scripts/claude_cost.sh\""
}
```

That's where a marketplace-installed plugin lands its files. The status line
lights up on the next interaction.

## How it works

Claude Code sends [JSON session data](https://code.claude.com/docs/en/statusline#available-data)
to the script on stdin after each assistant message (debounced at 300ms). The
script reads it, extracts a handful of fields with a single `jq` pass, formats
them, and writes one colored line to stdout. That's the whole contract — whatever
lands on stdout becomes the status line.

Because every value comes from the payload, there's nothing to keep in sync: when
Claude Code updates its cost estimate, the number here updates with it. The cost
is the same client-side estimate Claude Code shows elsewhere, so it may differ
slightly from your actual bill — that caveat is upstream's, not ours.

You can test the script the same way Claude Code invokes it:

```bash
echo '{"model":{"display_name":"Opus"},"context_window":{"used_percentage":25},"cost":{"total_cost_usd":0.42,"total_duration_ms":90000}}' | ./scripts/claude_cost.sh
```

## Customizing

The script is short and self-contained — edit `scripts/claude_cost.sh` directly.
Common tweaks:

- **Add fields**: the payload also carries `.context_window.remaining_percentage`,
  `.rate_limits.*`, `.workspace.repo.*`, git worktree info, and more. See the
  [full field list](https://code.claude.com/docs/en/statusline#available-data).
- **Change the color thresholds**: adjust the `70` / `90` cutoffs in the context
  section.
- **Go multi-line**: `print` more than one line and each becomes its own row.

Keep it fast — the script runs on every refresh, so slow work (heavy `git`
commands, network calls) will make the status line lag.
