---
name: your-token
description: Tracks how many tokens Claude has used, so it can warn before a limit hits and hand the work off cleanly. Covers claude.ai subscriptions (Free, Pro, Max, 5-hour window) and the Claude API (dollar spend, optional spend cap, rate limits), plus how full the current conversation's context is. It splits input from output and accounts for thinking, tool calls, how deeply files are read, reasoning effort, model and plan. Writes a HANDOFF.md and saves in-progress files when a limit is close. Use it whenever the user brings up token limits, usage limits, the 5-hour window, API cost or spend, rate limits, running out of quota or context, "your token", handoff, or checkpointing a long task. Also use it on long multi-step work (lots of tool calls, big files, high effort, an expensive model) that is likely to hit a limit, even if the user never names the skill.
license: MIT
compatibility: Needs Python 3 (standard library only) and a code execution tool to run the scripts. Without code execution it falls back to manual estimates.
metadata:
  author: Asa
  version: "1.0.0"
---

# your-token

You can't see the real remaining limit. This skill estimates usage, compares it with a budget, and lets you hand off cleanly before a limit hits. Anthropic publishes no exact free-tier numbers, so every claude.ai budget is a hypothesis that needs calibrating. The input/output split is dependable, because you know what you read and what you wrote. In API mode the numbers are exact for every call you log, since each API response carries a `usage` object.

Files: `plans.json` (plans, multipliers, models and prices, context window, effort profiles), `scripts/ledger.py`, `scripts/count_tokens.py`, `assets/handoff_template.md`. Standard-library Python only, no network, no subprocesses. They read `plans.json` and files you pass in, and write only the ledger file and an optional `--out` file such as `HANDOFF.md`.

Run commands as `python scripts/ledger.py ...` from the skill folder (use the full path if your shell resets the directory). The ledger lives at `~/.your-token/ledger.json` whatever the working directory is. Override it with env `YT_LEDGER` or `--ledger FILE` before the command.

## 0. Setup: no interview

The ledger creates itself on the first command (Free plan, Sonnet 5.5). Don't quiz the user; the one reset question below is the only exception. Pick the mode from context:

- claude.ai chat or app (the default): just log turns. If the user told you their plan, run `plan --set claude_pro|claude_max5|claude_max20`. If they told you when the window started, run `window --started-ago-min N`.
- Claude API, SDK, Agent SDK, or Claude Code with an API key: `init --api [--budget-usd N] [--period session|day|month] [--model M]`. A cap is optional.
- Ask one question about the reset, once per new chat (use buttons if you have them): "Roughly how long ago did your current 5-hour window start, or have you been idle 5h+?" If they say N minutes ago, run `window --started-ago-min N`. If they've been idle 5h or more, the window has already reset and usage is back to 0, so run `window --fresh`. If they're unsure, skip it and the window starts at the first logged turn. Skip the question in API mode, and when they've already told you. The reason: someone who left the chat alone for hours has a fresh window, and the ledger can't know that unless you ask.
- If you assumed the plan, say so once, in one line, with how to change it. Only ask if the user's goal depends on the answer (say, "how much can I still do today?" with the plan unknown).

The budget on claude.ai is `free_budget_tokens` times the plan multiplier: Pro at least 5x, Max 5x = 25x, Max 20x = 100x the Free baseline. The multiples come from Anthropic. The Free baseline is unverified. Weekly limits on Max plans aren't tracked.

## 1. Two clocks

| Clock | Measures | Resets when |
|---|---|---|
| USAGE | claude.ai: weighted tokens in the rolling 5h window vs the budget. API: USD spend in the period vs an optional cap | the window or period ends (API `session` never auto-resets) |
| CONTEXT | how full this conversation is (default limit 200k, change it with `calibrate --context-limit`) | only in a new chat or session |

When USAGE is full, wait for the reset (or upgrade, or raise the cap). When CONTEXT is full, continue in a new chat from HANDOFF.md. A new window doesn't empty the context, and a new chat doesn't refresh the window.

## 2. Classify by source

Reading is input and generating is output. Input is the system prompt, the user, and tool results. Output is assistant text, tool calls, thinking (hidden but billed), and created files.

## 3. Cost model

- Every model call re-reads the whole context (k tool calls means k+1 reads), so in a big conversation even a one-line follow-up costs a lot. The ledger reports the MARGINAL COST of the next message and a FORECAST of turns left here against a fresh chat.
- claude.ai: `(input + output x out_ratio) x usage_weight`. `out_ratio` is the output/input price ratio (5 for every current model). `usage_weight` (relative to Sonnet 5.5) is a guess. Adjust it with `calibrate --model-weight opus-5.5=1.8`.
- API USD: `(input x price_in + output x price_out) / 1e6`. For exact usage, cache reads cost 0.1x, 5-minute cache writes 1.25x, 1-hour writes 2x, and Batch 0.5x (`plans.json` > `api_pricing`). Thinking is billed as output and is already inside `usage.output_tokens`.
- Prices go stale. `plans.json` has `prices_as_of`, and status warns after 90 days. After checking the pricing page, update with `calibrate --price opus-5.5=4/20` (USD per MTok).
- Cache: the discount is `cache_discount` (Free: 0). It's dropped for a turn when the pause since the previous turn is over 5 minutes, measured automatically or set with `--gap-min`.
- Reading depth: `skim` counts only what entered the context. `deep` counts the whole file times the number of re-reads. Thinking grows with effort (`thinking_ratio`).
- The Free default of 30000 per window is an unverified third-party estimate. Calibration replaces it.

## 4. Read your own settings

At session start, and whenever they change, check your effort and your model.

Effort: read the `reasoning_effort` tag in your own context on every turn, and look at it before you log. The scale is 4=low, 5=medium, 10=high, 30=extra, max=max. Pass that value as `--effort`. If the tag is missing, leave `--effort` out and it defaults to 5=medium. Never guess from memory or earlier turns, because it can change between messages.

Model: take it from your system prompt or the API request, for example "Claude Opus 5.5" or `claude-opus-5-5`, which maps to `opus-5.5`. Pass `--model` on the turn it changes. The script warns if the model isn't normally on the plan.

## 5. Workflow

Language. Script output is English. Relay it to the user in the language they write in, in plain words. Don't paste raw ledger output unless they ask for it.

Keep tracking cheap, because every logging call costs tokens too. Log one `turn` after turns that used tools or produced long output. For a run of short chat-only turns, log them together: `turn --turns 3 --user-chars <sum> --out-chars <sum>`. When all is OK the script prints one short line. The full status only appears at WARN or HANDOFF, after a reset, or with `--verbose`.

### claude.ai (code execution available)
1. Before a big task: `predict --effort N [--tool-calls K --out-chars C --result-tokens R --model M]`. If USAGE or CONTEXT would cross the handoff threshold, lower the effort or the model, split the task, or hand off first.
2. When reading files: `add --type tool_result --file F [--lines a-b | --read-mode skim|deep --times N]`.
3. After a turn, make one call and pass character counts, never conversation text:
   `turn [--effort N] --tool-calls K --user-chars U --out-chars O [--created-chars C] [--result-tokens R] --lang vi|en|code|mixed [--model M] [--turns N]`
   (`--lang` only picks the chars-per-token class: `vi` Vietnamese, `en` English-like, `code`, `mixed`.)

### Claude API / agents
1. After each API response: `api-usage --input <input_tokens> --output <output_tokens> [--cache-read N] [--cache-write N] [--cache-write-1h N] [--batch] [--model M]`. This sets CONTEXT exactly and adds exact dollars. If you don't have the `usage` object, fall back to `turn` and the dollars become estimates.
2. Once you know them (optional): `calibrate --budget-usd N`, and `calibrate --rpm N --itpm N --otpm N` for the limits in the Console. Status then shows `RATE (last 60s)` and warns at 80%.
3. On an HTTP 429, run `calibrate --hit-rate-limit` right away (it records the observed rates as the limits), then back off and retry. If you hit a spend cap, run `calibrate --hit-limit`.
4. `--period day|month` resets the cap every 24 hours or 30 days from the first call. `session` (the default) never resets until you run `reset`.

### Acting on the output (both modes)
- `** NEW 5H WINDOW` or `** NEW 24H PERIOD`: usage reset. Tell the user in one line.
- A single short `USAGE ... | CONTEXT ...` line saying OK: carry on and say nothing.
- `WARN` (70% or more): give a one-line heads-up, prioritize, and pass on one or two of the SAVING TIPS.
- `HANDOFF` (85% or more): finish the smallest clean unit (never stop mid-file or mid-function), then hand off (section 6). Say which clock triggered it. If it's USAGE, the user can also wait for the reset (the time is shown) or, on the API, raise the cap. If it's CONTEXT, a new chat or session is the only fix.
- `=> OPEN A NEW CHAT`: the next message already costs 10% or more of the budget and a fresh chat would last longer. Write HANDOFF.md now, even if USAGE is low.
- `NO_BUDGET` (claude.ai): you only get totals. Set a budget with `calibrate --budget N` or keep tracking. `NO_CAP` (API) is normal, and spend is still reported.

No code execution: estimate by hand. Use chars/2.8 for Vietnamese, /4 for English, /3.3 for code. Multiply output by 5, count a context re-read for each tool call, and treat expensive models as 2 to 5 times the cost. API dollars are tokens times the list price. Hand off at about 85% of whatever budget the user gave you.

## 6. Handoff

1. Copy `assets/handoff_template.md` to `HANDOFF.md` and fill in every section with specifics: the exact stopping point, next steps in order, decisions with their reasons, the file list.
2. Run `python scripts/ledger.py export --out HANDOFF.md`, which appends the ledger block (plan, model, calibration).
3. Put `HANDOFF.md` and every in-progress file in `/mnt/user-data/outputs/` and show them with `present_files`. Then tell the user in 2 or 3 lines how far you got, which clock triggered it, and what to do next (new chat and attach the files, or wait for the reset). If you can't present files, write them to the working directory and give the paths.
4. In the new chat: `restore --file HANDOFF.md`. Plan, model and calibration carry over, and you don't need to ask anything.
5. After the user resets manually: `reset` (usage only) or `reset --new-chat` (usage and context). Budget, weights and calibration stay.

## 7. Calibration

- If you know the budget: `calibrate --budget N` (claude.ai, per 5h window, Sonnet-equivalent) or `--budget-usd N` (API).
- When the user says "I just hit the limit", run `calibrate --hit-limit` before any reset. On claude.ai the observations are normalized by plan multiplier, so they survive a plan change.
- Other options: `--model-weight NAME=VALUE`, `--price NAME=IN/OUT`, `--context-limit N` (for example 1000000), `--cache-discount X`, `--rpm/--itpm/--otpm`.
- claude.ai estimates can be off by tens of percent. That's why the thresholds sit below 100%.

## 8. Don'ts

- Don't claim to know the true remaining limit. API dollars are exact only for the calls you logged.
- Don't interrogate the user to set this up. Use the defaults and fix them when told.
- Don't let tracking cost more than it saves. Batch quiet turns with `--turns` and run `status` only when you need it.
- Don't announce routine OK status. Speak up at WARN, HANDOFF, a reset, new-chat advice, or when asked.
