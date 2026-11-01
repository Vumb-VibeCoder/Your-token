# your-token

![License: MIT](https://img.shields.io/badge/license-MIT-green?style=flat-square)
![Python 3](https://img.shields.io/badge/python-3-blue?style=flat-square)
![Agent Skills](https://img.shields.io/badge/Agent%20Skills-compatible-8A2BE2?style=flat-square)
![Claude](https://img.shields.io/badge/for-Claude-D97757?style=flat-square)
[![tests](https://github.com/Vumb-VibeCoder/Your-token/actions/workflows/test.yml/badge.svg)](https://github.com/Vumb-VibeCoder/Your-token/actions/workflows/test.yml)

Claude can't see how close you are to your usage limit. You find out when the chat stops, usually halfway through a file.

This skill gives it a running tally. When the numbers get close, Claude finishes the piece it's on, writes a `HANDOFF.md`, saves the half-done files, and you carry on in a fresh chat without re-explaining anything.

This is what the script prints when a 5-hour window is nearly used up (trimmed real output: Pro plan, Sonnet 5.5, four tool-heavy turns):

```
USAGE  : input_billed~101475 output~8460 weighted~143775
         96% of window budget (150000) -> HANDOFF
CONTEXT: ~21060/200000 = 11% -> OK
=> USAGE nearly out: stop new work at a clean point, write HANDOFF.md and send the files now. Window resets in ~3h24m.
```

Claude reads that, wraps up cleanly, and tells you in your own language. It works on claude.ai (Free, Pro, Max) and on the API.

## Two meters, not one

Usage and context run out separately, and fixing one does nothing for the other.

- **Usage.** On claude.ai this is weighted tokens inside the rolling 5-hour window. On the API it's dollars against a cap you pick (per session, day or month), plus requests and tokens per minute if you give it your rate limits.
- **Context.** How full this particular conversation is. Waiting for the window to reset won't help, and only a new chat clears it.

It also separates input from output, counts extended thinking and tool calls, tells skimming a file from reading it properly, and factors in reasoning effort, plan and model (Haiku 5.5, Sonnet 5.5, Opus 5.5, Fable 5.1, Mythos 5.1).

One more thing it does: every model call re-reads the whole context, so a long chat makes even a one-line follow-up expensive. The ledger works out what your next message will cost and how many turns you have left here versus in a fresh chat, and says so when starting over is cheaper.

## How much to trust the numbers

Anthropic doesn't publish exact claude.ai limits, so a good part of this is educated guessing.

- The Free baseline of 30,000 weighted tokens per window comes from third-party estimates. If you hit a limit, tell Claude "I just hit the limit" and it runs `calibrate --hit-limit`. The observation is normalized by plan multiplier, so it still counts if you change plan.
- `usage_weight` per model is a rough heuristic pegged to API list prices (USD per million tokens, in/out): Haiku 5.5 0.10/0.50, Sonnet 5.5 2/10, Opus 5.5 4/20, Fable 5.1 10/50. Mythos 5.1 has no published price, so it's treated as Fable.
- Prices go stale. `status` complains once `prices_as_of` in `plans.json` is more than 90 days old. Fix a price with `calibrate --price opus-5.5=4/20`.
- On the API the dollar figures are exact for every response whose `usage` object you feed in. That includes cache reads at 0.1x, 5-minute cache writes at 1.25x, 1-hour writes at 2x and Batch at 0.5x.
- A pause longer than 5 minutes expires the prompt cache, so the next turn pays full price for everything it re-reads.
- Max plans have weekly limits. This skill doesn't track them.

## Setup

There isn't any. The first command creates `~/.your-token/ledger.json` (Free plan, Sonnet 5.5), and it's the same file from any working directory. Point it somewhere else with the `YT_LEDGER` env var or `--ledger FILE` before the command.

Tell Claude your plan whenever it comes up, or use `init --api` for API work. Claude mentions its assumption once and doesn't quiz you. The only thing it might ask, once per new chat, is roughly when your current 5-hour window started, because the script can't know that on its own.

Script output is in English. Claude relays it in whatever language you're writing in.

## Install

Pick whichever matches where you use Claude.

Claude Code, as a plugin marketplace:

```
/plugin marketplace add Vumb-VibeCoder/Your-token
/plugin install your-token@your-token
```

Claude Code, by hand: copy `skills/your-token` into `~/.claude/skills/` (or a project's `.claude/skills/`).

claude.ai: upload a zip of the skill folder under Skills in your settings. GitHub's "Download ZIP" won't work for this, because its root folder is `your-token-main` and holds the whole repo, while the upload wants the skill folder named to match `name: your-token`. Build the right one:

```bash
python tools/package.py        # writes dist/your-token.zip, root folder your-token/
```

Code Execution has to be on for Claude to run the scripts. Without it Claude falls back to the hand estimates in `SKILL.md`, which are rougher but work.

## Repo layout

```
skills/your-token/    the skill itself (SKILL.md, plans.json, scripts/, assets/)
tests/                unit and end-to-end tests, plus a check against the Agent Skills spec
tools/package.py      builds the upload zip
.claude-plugin/       marketplace manifest for Claude Code
```

The skill follows the [Agent Skills spec](https://agentskills.io/specification): `SKILL.md` with `name`, `description`, `license`, `compatibility` and `metadata`, and the optional folders named as the spec says.

## Commands

Run these from `skills/your-token/` (Claude does the same).

```bash
# claude.ai (the ledger creates itself, init is optional)
python scripts/ledger.py init --plan claude_pro --model "Claude Opus 5.5"
python scripts/ledger.py window --started-ago-min 40      # or: window --fresh  (idle 5h+, window already reset)
python scripts/ledger.py turn --effort 5 --tool-calls 2 --user-chars 800 --out-chars 2400 --lang en
python scripts/ledger.py turn --turns 3 --user-chars 300 --out-chars 1500 --lang vi   # 3 quiet turns, one call
python scripts/ledger.py predict --effort 10 --model fable-5.1 --out-chars 6000
python scripts/ledger.py plan --set claude_max5 --model opus-5.5
python scripts/ledger.py calibrate --hit-limit

# API
python scripts/ledger.py init --api --budget-usd 5 --period day --model claude-sonnet-5-5
python scripts/ledger.py api-usage --input 1200 --output 800 --cache-read 40000 --cache-write 3000
python scripts/ledger.py calibrate --rpm 50 --itpm 30000 --otpm 8000          # from your Console limits
python scripts/ledger.py calibrate --hit-rate-limit                           # right after a 429
python scripts/ledger.py calibrate --price opus-5.5=4/20                      # refresh a list price

# both
python scripts/ledger.py status
python scripts/ledger.py export --out HANDOFF.md
python scripts/ledger.py restore --file HANDOFF.md
python scripts/ledger.py reset [--new-chat]
```

## What it touches

Standard-library Python and nothing else. No network, no subprocesses. The scripts read `plans.json` and any file you pass them, and the only things they write are the ledger and an optional `--out` file like `HANDOFF.md`. Everything is plain text you can open and read.

Tracking costs tokens too, so it stays quiet: when everything is fine the script prints a single short line, and a run of short chat-only turns can be logged in one go with `turn --turns N`.

## Tests

```bash
python -m unittest discover -s tests -v
```

## Contributing

Issues and pull requests are welcome. `CONTRIBUTING.md` has the short version.

## License

MIT, see `LICENSE`. Copyright Asa.
