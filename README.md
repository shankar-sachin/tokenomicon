<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/tokenomicon-wordmark-dark.svg">
  <img alt="Tokenomicon" src="assets/tokenomicon-wordmark.svg" width="480">
</picture>

*Every token you summoned. And what it cost you.*

Scan your Claude Code sessions and see where the money went. It reads the transcripts Claude Code already writes to `~/.claude/projects`, prices them offline, and nothing leaves your machine.

It has two modes, and the toggle in the header flips between them:

- **Sessions**: pick one session or a bunch and get an itemized bill (fresh input, cache writes, cache reads, output, cost per model, cost per API call, and how long it all took).
- **Burn**: the whole picture at once. Spend per day, by project, by model and by activity, your cache hit rate and your one-shot rate, filterable by date range, project and model.

## Run it

```bash
python3 tokenomicon.py --serve
```

That scans every transcript and opens http://localhost:8787. Refresh the page to rescan, and your selection sticks. Keep `tokenomicon.py` and `tokenomicon.html` in the same folder.

Hit **Live Track** on that page and it keeps watching: every 3 seconds it asks the server what changed, so the cost, tokens and cache numbers for your selection climb while Claude works. Sessions running right now get a pulsing dot, the session you're working in gets ticked for you, and a bar tracks how much you've spent since you turned it on. It stays on across refreshes until you turn it off. It only re-reads transcripts that changed, so leaving it on is cheap. It needs `--serve`, so it does nothing in a saved report file.

Rather have a file? `python3 tokenomicon.py` writes `tokenomicon-report.html` with everything baked in and opens it.

Don't want to run a script? Open `tokenomicon.html` and hit **Load folder** (pick `~/.claude/projects`; on a Mac, `Cmd+Shift+.` shows hidden folders), drag the folder onto the page, or click **Demo data**.

Want it on your phone too? `python3 tokenomicon.py --serve --host 0.0.0.0` makes it reachable from other devices on your Wi-Fi. Heads up: that means anyone on the same network can see your spend and your prompts.

Flags: `--dir PATH` (scan somewhere else, repeatable), `--cloud FILE` (cloud export to include, repeatable), `--out FILE`, `--no-open`, `--serve`, `--port N` (default 8787), `--host ADDR` (default 127.0.0.1).

### Cloud sessions

Sessions you start from the Claude desktop app, claude.ai/code, your phone or a project thread run in Anthropic's cloud, so their transcripts never land in `~/.claude/projects` and the scanner can't see them. Claude does keep running totals for each one (fresh input, cache writes, cache reads, output and the dollar figure), and you can bring those in with a cloud export:

- Ask any Claude Code session that has the session tools to write a `tokenomicon-cloud.json` (format below), or grab one a previous session made.
- Drop it next to `tokenomicon.py` and it's picked up on every scan or refresh, or drag it onto the page.

Cloud sessions show up with a "cloud" tag. They only have totals, so there's no per-call breakdown and no active time, and the cost is Claude's own number, split across token types using list prices.

```json
{"tokenomicon": "cloud-sessions", "version": 1, "sessions": [
  {"id": "session_...", "title": "Model comparison index and scoring", "project": "eval-forge",
   "repos": ["eval-forge/claude-battle"], "origin": "desktop", "model": "claude-fable-5-2",
   "start": "2026-11-10T01:47:34Z", "end": "2026-11-11T21:24:21Z",
   "usage": {"input_tokens": 2635, "output_tokens": 42460, "cache_read_tokens": 9899299,
             "cache_write_tokens": 258186, "cost_usd": 7.83}}
]}
```

Regular claude.ai chats aren't included: nothing Anthropic exposes for them carries token counts, so there's nothing honest to bill.

![Tokenomicon with demo data](assets/preview.png)

## How it works

1. **Pick sessions.** The left panel lists every session it found under the same name Claude Code's `/resume` shows (your `/rename` title, else Claude's auto-generated title, else the first prompt), plus project, date, time spent, cost and token count. Hover a name to see the first prompt. Search, filter by project or date (24h / 7d / 30d / All), sort by newest, priciest, most tokens or longest, and tick as many as you like. "Select all shown" grabs everything that matches your filter.
2. **Read the bill.** The right panel breaks down whatever you picked:
   - Total cost, fresh input, cache writes, cache reads, output (with thinking tokens called out) and time
   - Share of tokens vs share of cost, side by side, so you can see cache reads being 96% of the tokens and a fraction of the money
   - A table of every token type with its count, the effective rate you paid per million, its cost and its share
   - Cost per API call for one session, or cost per session when you pick several, stacked by token type with hover receipts
   - Totals per model, and a per-session table you can prune
3. **Take it with you.** Download CSV exports the selected sessions with every token and dollar column.

### Burn mode

- **KPIs**: total spend and daily average, sessions and API calls, tokens, cache hit rate (and what caching saved you), one-shot rate, and average cost per message you sent.
- **Spend per day**: stacked by token type, hourly for the last 24h, daily up to about three months, weekly beyond that.
- **Where it went**: every message you sent, sorted by what Claude did to answer it: Coding, Debugging, Refactoring, Testing, Docs, Exploration, Planning, Git & PRs, Build & deploy, Delegation or Conversation. It's a heuristic built from the tools Claude called (edits, bash commands like `pytest` or `git push`, file types, subagents) and a few keywords in your prompt, so treat it as a good guess, not an audit.
- **One-shot rate**: of the turns where Claude edited files, the share where it didn't hit a tool error and then edit again, and your next message didn't sound like "still broken" or "try again".
- **By project / by model / priciest sessions**: click a project to filter everything to it, click a session to jump to its itemized bill in Sessions mode.

![Burn mode with demo data](assets/preview-burn.png)

Live Track works in both modes. Cloud sessions count toward spend, projects and models, but they only have totals, so they get their own row in the activity table and don't count toward the one-shot rate.

## How the numbers work

- **Cost** uses Anthropic API list prices (the `PRICING` table at the top of the script in `tokenomicon.html`). Cache writes are billed at 1.25x input (5 min TTL) or 2x (1 hour TTL), cache reads at each model's cache rate, web searches at $10 per thousand, and fast mode at 2x. On a Pro or Max subscription this is what the same usage would cost on the API.
- **Local models** (anything that isn't a Claude model, like a GGUF you run through a proxy) count as $0. Unknown Claude models are priced as Opus 5.5.
- **Duplicates**: Claude Code writes one line per content block, all carrying the same usage. Tokenomicon counts each API message once.
- **Subagents** roll up into their parent session.
- **Active time** adds up the gaps between events and skips any gap over 5 minutes. **Wall** is first event to last.

## Files

| File | What it is |
| --- | --- |
| `tokenomicon.html` | The dashboard (a single self-contained file) |
| `tokenomicon.py` | Collector: parses transcripts, dedupes, classifies activity and bakes compact totals into a report (a few MB even for months of logs) |
| `assets/tokenomicon-logo.svg` | App icon |
| `assets/tokenomicon-wordmark.svg` / `-dark.svg` | Logo and name, for light and dark backgrounds |
| `assets/preview.png` | Sessions mode with four demo sessions selected |
| `assets/preview-burn.png` | Burn mode with demo data |

Generated `tokenomicon-report.html` files and `tokenomicon-cloud*.json` exports hold your own usage data, so `.gitignore` keeps them out of the repo.
