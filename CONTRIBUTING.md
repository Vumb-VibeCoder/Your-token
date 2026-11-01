# Contributing

Small project, small rules.

Run the tests before you open a PR:

```bash
python -m unittest discover -s tests -v
```

A few things to keep in mind:

- The scripts use the standard library only. Please don't add dependencies.
- `SKILL.md` leans on specific words in the script output (`WARN`, `HANDOFF`, `OPEN A NEW CHAT`, `NEW 5H WINDOW`, `NO_BUDGET`, `NO_CAP`). If you reword one, change `SKILL.md` in the same PR.
- If you touch `SKILL.md`, `tests/test_spec.py` checks it against the Agent Skills spec: name format, description under 1024 characters, and so on.
- Numbers in `plans.json` are estimates. If you have a better source for one, put the link in the PR.
- Write comments and docs the way you'd explain the thing to a coworker. Plain words, say what you actually know and what you're guessing.

Found a bug? Open an issue and include the command you ran and what it printed.
