"""End-to-end checks: every test drives the scripts the way Claude does, with a throwaway ledger."""
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "skills" / "your-token" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import count_tokens  # noqa: E402


class LedgerCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = {**os.environ, "YT_LEDGER": str(Path(self.tmp.name) / "ledger.json")}

    def tearDown(self):
        self.tmp.cleanup()

    def run_ledger(self, *args, ok=True):
        r = subprocess.run([sys.executable, str(SCRIPTS / "ledger.py"), *args],
                           capture_output=True, text=True, env=self.env)
        if ok:
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        else:
            self.assertNotEqual(r.returncode, 0)
            self.assertNotIn("Traceback", r.stderr)
        return r.stdout + r.stderr


class Claudeai(LedgerCase):
    def test_first_command_creates_ledger(self):
        out = self.run_ledger("turn", "--user-chars", "800", "--out-chars", "2400", "--lang", "en")
        self.assertIn("Ledger created", out)
        self.assertTrue(Path(self.env["YT_LEDGER"]).exists())

    def test_window_fresh(self):
        self.assertIn("Window 5h", self.run_ledger("window", "--fresh"))

    def test_plan_multiplier(self):
        self.assertIn("x25", self.run_ledger("plan", "--set", "claude_max5", "--model", "opus-5.5"))

    def test_heavy_use_reaches_handoff(self):
        self.run_ledger("init", "--plan", "claude_free")
        out = ""
        for _ in range(4):
            out = self.run_ledger("turn", "--effort", "10", "--tool-calls", "6", "--user-chars", "1200",
                                  "--out-chars", "9000", "--result-tokens", "14000", "--lang", "en")
        self.assertIn("HANDOFF", out)

    def test_hit_limit_sets_budget(self):
        self.run_ledger("turn", "--user-chars", "800", "--out-chars", "2400", "--lang", "en")
        self.assertIn("Limit hit", self.run_ledger("calibrate", "--hit-limit"))


class Api(LedgerCase):
    def test_exact_dollars(self):
        self.run_ledger("init", "--api", "--model", "claude-sonnet-5-5")
        out = self.run_ledger("api-usage", "--input", "1200", "--output", "800",
                              "--cache-read", "40000", "--cache-write", "3000")
        self.assertIn("+$0.025900", out)

    def test_one_hour_cache_write_and_batch(self):
        self.run_ledger("init", "--api", "--model", "claude-sonnet-5-5")
        out = self.run_ledger("api-usage", "--input", "1200", "--output", "800",
                              "--cache-write-1h", "500", "--batch")
        self.assertIn("+$0.006200", out)

    def test_cap_is_reported(self):
        self.run_ledger("init", "--api", "--budget-usd", "5", "--period", "day")
        self.run_ledger("api-usage", "--input", "1000", "--output", "1000")
        self.assertIn("of $5 cap", self.run_ledger("status"))


class Handoff(LedgerCase):
    def test_export_restore_roundtrip(self):
        self.run_ledger("init", "--api", "--budget-usd", "5", "--model", "claude-sonnet-5-5")
        self.run_ledger("api-usage", "--input", "1200", "--output", "800")
        f = str(Path(self.tmp.name) / "HANDOFF.md")
        self.run_ledger("export", "--out", f)
        os.remove(self.env["YT_LEDGER"])
        self.assertIn("mode=api", self.run_ledger("restore", "--file", f))


class BadInput(LedgerCase):
    def test_bad_effort(self):
        self.run_ledger("turn", "--effort", "banana", "--user-chars", "1", "--out-chars", "1", ok=False)

    def test_unknown_model(self):
        self.run_ledger("turn", "--model", "nonsense-9", "--user-chars", "1", "--out-chars", "1", ok=False)

    def test_zero_turns(self):
        self.run_ledger("turn", "--turns", "0", "--user-chars", "1", "--out-chars", "1", ok=False)

    def test_zero_budget(self):
        self.run_ledger("calibrate", "--budget", "0", ok=False)

    def test_missing_files(self):
        self.run_ledger("add", "--type", "tool_result", "--file", "/nonexistent", ok=False)
        self.run_ledger("restore", "--file", "/nonexistent", ok=False)

    def test_corrupt_ledger(self):
        Path(self.env["YT_LEDGER"]).write_text("{bad", encoding="utf-8")
        self.run_ledger("status", ok=False)


class CountTokens(unittest.TestCase):
    def test_language_detection(self):
        self.assertEqual(count_tokens.detect_lang("xin chào các bạn, hôm nay trời đẹp"), "vi")
        self.assertEqual(count_tokens.detect_lang("The quick brown fox jumps over the lazy dog"), "en")
        self.assertEqual(count_tokens.detect_lang("x", filename="main.py"), "code")

    def test_vietnamese_costs_more_per_char(self):
        self.assertGreater(count_tokens.chars_to_tokens(1000, "vi"), count_tokens.chars_to_tokens(1000, "en"))


class Packaging(unittest.TestCase):
    def test_zip_root_matches_skill_name(self):
        subprocess.run([sys.executable, str(REPO / "tools" / "package.py")], check=True, capture_output=True)
        names = zipfile.ZipFile(REPO / "dist" / "your-token.zip").namelist()
        self.assertTrue(all(n.startswith("your-token/") for n in names))
        for needed in ("SKILL.md", "LICENSE", "plans.json", "scripts/ledger.py", "assets/handoff_template.md"):
            self.assertIn(f"your-token/{needed}", names)


class Ingest(LedgerCase):
    def transcript(self, rows):
        import json
        f = Path(self.tmp.name) / "s.jsonl"
        f.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
        return str(f)

    @staticmethod
    def row(mid, out, w5=0, w1=0, read=0, ts=None):
        import time
        ts = ts or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 60))
        return {"type": "assistant", "timestamp": ts, "message": {"id": mid, "model": "claude-sonnet-5-5", "usage": {
            "input_tokens": 10, "output_tokens": out, "cache_read_input_tokens": read,
            "cache_creation_input_tokens": w5 + w1,
            "cache_creation": {"ephemeral_5m_input_tokens": w5, "ephemeral_1h_input_tokens": w1}}}}

    def test_exact_counts_dedupe_and_one_hour_ttl(self):
        f = self.transcript([self.row("a", 5, w1=1000), self.row("a", 50, w1=1000), self.row("b", 20, read=1000)])
        out = self.run_ledger("ingest", "--transcript", f)
        self.assertIn("ingested 2 calls", out)
        self.assertIn("60 min (observed)", out)

    def test_five_minute_tier_detected_and_only_new_lines(self):
        f = self.transcript([self.row("a", 5, w5=1000)])
        self.assertIn("5 min (observed)", self.run_ledger("ingest", "--transcript", f))
        self.assertIn("ingested 0 calls", self.run_ledger("ingest", "--transcript", f))

    def test_manual_ttl(self):
        self.assertIn("60 min", self.run_ledger("calibrate", "--cache-ttl-min", "60"))

    def test_missing_transcript(self):
        self.run_ledger("ingest", "--transcript", "/nonexistent.jsonl", ok=False)


if __name__ == "__main__":
    unittest.main()
