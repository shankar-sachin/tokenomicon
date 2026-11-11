<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/tokenomicon-wordmark-dark.svg">
  <img alt="Tokenomicon" src="assets/tokenomicon-wordmark.svg" width="480">
</picture>

*Every token you summoned. And what it cost you.*

A local, zero-dependency dashboard for your Claude Code usage: tokens in and out, cost per session, per day, per model and per project, and how long each session actually took. It reads the transcripts Claude Code already writes to `~/.claude/projects`, and nothing leaves your machine.

## Run it

```bash
python3 tokenomicon.py
```

That scans every transcript, bakes the numbers into `tokenomicon-report.html` and opens it. Keep `tokenomicon.py` and `tokenomicon.html` in the same folder.

Don't want to run a script? Open `tokenomicon.html` and hit **Load folder** (pick `~/.claude/projects`; on a Mac, `Cmd+Shift+.` shows hidden folders), drag the folder onto the page, or click **Demo data**.

Want it live instead of a file? Run the built-in server, and every refresh rescans your logs:

```bash
python3 tokenomicon.py --serve                 # http://localhost:8787
python3 tokenomicon.py --serve --host 0.0.0.0  # also reachable from other devices on your Wi-Fi
```

Heads up: `--host 0.0.0.0` means anyone on the same network can see your spend and your first prompts.

Flags: `--dir PATH` (scan somewhere else, repeatable), `--out FILE`, `--no-open`, `--serve`, `--port N` (default 8787), `--host ADDR` (default 127.0.0.1).

![Tokenomicon with demo data](assets/preview.png)

## What it shows

- Total spend, sessions, input and output tokens, active and wall-clock time, and how much prompt caching saved you
- Cost per day (per week past 120 days), stacked by model
- Cost by model and by project
- Token mix: fresh input, cache writes, cache reads, output
- A sortable, searchable session table with each session's first prompt

Filters: 7d / 30d / 90d / All, project, and model.

## How the numbers work

- **Cost** uses Anthropic API list prices (the `PRICING` table at the top of the script in `tokenomicon.html`). Cache writes are billed at 1.25x input (5 min TTL) or 2x (1 hour TTL), cache reads at each model's cache rate, and fast mode at 2x. On a Pro or Max subscription this is what the same usage would cost on the API.
- **Duplicates**: Claude Code writes one line per content block, all carrying the same usage. Tokenomicon counts each API message once.
- **Subagents** roll up into their parent session.
- **Active time** adds up the gaps between events and skips any gap over 5 minutes. **Wall** is first event to last.

## Files

| File | What it is |
| --- | --- |
| `tokenomicon.html` | The dashboard (a single self-contained file) |
| `tokenomicon.py` | Collector that builds a report with your data baked in |
| `assets/tokenomicon-logo.svg` | App icon |
| `assets/tokenomicon-wordmark.svg` / `-dark.svg` | Logo and name, for light and dark backgrounds |
| `assets/preview.png` | Screenshot with demo data |

Generated `tokenomicon-report.html` files hold your own usage data, so `.gitignore` keeps them out of the repo.
