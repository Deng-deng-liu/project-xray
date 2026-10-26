import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "examples" / "spring-demo"
SAMPLE_REPORT = ROOT / "examples" / "sample-report.md"
ARTIFACTS = ROOT / "tests" / ".artifacts"
ARTIFACTS.mkdir(parents=True, exist_ok=True)


def run_cli(*args):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    return subprocess.run(
        [sys.executable, "-m", "project_xray", *map(str, args)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )


class ProjectXrayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.signals = ARTIFACTS / "signals.json"
        proc = run_cli("scan", FIXTURE, "--out", cls.signals)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr or proc.stdout)
        cls.data = json.loads(cls.signals.read_text(encoding="utf-8"))

    def test_scan_detects_expected_rules(self):
        rules = {s["rule_id"] for s in self.data["signals"]}
        expected = {
            "JAVA-EMPTY-CATCH",
            "JAVA-DB-CALL-IN-LOOP",
            "JAVA-TX-REMOTE-CALL",
            "CONFIG-HARD-CODED-SECRET",
            "JAVA-HTTP-NO-TIMEOUT",
            "SQL-LIMIT-WITHOUT-ORDER",
            "JAVA-THREAD-SLEEP",
            "JAVA-SYNCHRONIZED",
            "SRC-TODO",
        }
        self.assertTrue(expected.issubset(rules), expected - rules)

    def test_render_creates_code_cards(self):
        assets = ARTIFACTS / "assets"
        proc = run_cli("render", self.signals, "--out-dir", assets)
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertTrue((assets / "E001.png").is_file())
        self.assertTrue((assets / "manifest.json").is_file())
        manifest = json.loads((assets / "manifest.json").read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(manifest["images"]), 3)

    def test_fix_renderer_creates_after_card(self):
        assets = ARTIFACTS / "fix-assets"
        proc = run_cli("fixes", self.signals, ROOT / "examples" / "fixes.json", "--out-dir", assets)
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertTrue((assets / "E001-after.png").is_file())
        self.assertTrue((assets / "fix-manifest.json").is_file())

    def test_sample_report_passes_validation(self):
        proc = run_cli("validate", SAMPLE_REPORT, self.signals)
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)

    def test_report_without_images_is_rejected(self):
        bad = ARTIFACTS / "bad-report.md"
        text = SAMPLE_REPORT.read_text(encoding="utf-8")
        lines = [line for line in text.splitlines() if not line.lstrip().startswith("![")]
        bad.write_text("\n".join(lines) + "\n", encoding="utf-8")
        proc = run_cli("validate", bad, self.signals)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("no code evidence images", proc.stderr)

    def test_doctor(self):
        proc = run_cli("doctor")
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertIn("project-xray", proc.stdout)
        self.assertIn("status: OK", proc.stdout)


class SecretRedactionTests(unittest.TestCase):
    def test_scan_redacts_fixture_secrets(self):
        signals = ARTIFACTS / "secret-signals.json"
        proc = run_cli("scan", FIXTURE, "--out", signals)
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        blob = signals.read_text(encoding="utf-8")
        self.assertNotIn("SuperSecret123", blob)
        self.assertIn("***REDACTED***", blob)
