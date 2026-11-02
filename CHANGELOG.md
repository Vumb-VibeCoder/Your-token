# Changelog

## Unreleased

- New `ingest --transcript FILE`: logs exact `message.usage` from Claude Code transcripts instead of estimating (dedupes streamed lines, reads only new lines).
- Cache TTL is no longer hardcoded to 5 min: `ingest` and `api-usage` set it from the billed write tier (`ephemeral_1h` vs `ephemeral_5m`); override with `calibrate --cache-ttl-min`. Saving tips use the live TTL.

## 1.0.0

- First public release.
- Tracks claude.ai usage (5-hour window) and API spend, plus how full the context is.
- Writes a `HANDOFF.md` and saves in-progress files before a limit hits.
- Follows the Agent Skills spec; ships a Claude Code marketplace manifest, tests and CI.
