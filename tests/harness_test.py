# -*- coding: utf-8 -*-
"""Tests of the test support module itself (`_harness.py`): the evidence line a test leaves when it ends must say what
really happened — a failing case-based test must not be recorded as a pass, and a run that executed nothing must not
leave a pass behind.
"""
import _harness  # noqa: F401,E402 — collects the temp folders in one place for this test and removes it at the end
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

for _key in ("PYTHONIOENCODING", "PYTHONUTF8"):
    os.environ.pop(_key, None)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = Path(__file__).resolve().parent
failures = []


def check(name, condition, text=""):
    print(("  ok  " if condition else "  FAIL  ") + name)
    if not condition:
        failures.append(name)
        if text != "":
            print("        " + str(text)[:600].replace("\n", " / "))


CASE_TEST = ("import _harness\n"
             "failures = []\n"
             "def finish():\n"
             "    print('failures: %d' % len(failures))\n"
             "    return 1 if failures else 0\n"
             "raise SystemExit(_harness.run_cases(__file__, globals(), finish))\n"
             "# case: good\n"
             "print('  ok  good')\n"
             "# case: bad\n"
             "print('  FAIL  bad')\n"
             "failures.append('bad')\n")
PLAIN_TEST = "import sys\nimport _harness\nprint('  FAIL  plain')\nsys.exit(1)\n"
# Endings that tell the harness nothing: its exit code is set only by the wrapped sys.exit, by the exception hook and by run_cases
BARE_FAIL_TEST = "import _harness\nprint('  FAIL  bare')\nraise SystemExit(1)\n"
BARE_ZERO_TEST = "import _harness\nprint('  ok  bare')\nraise SystemExit(0)\n"
FALL_OFF_TEST = "import _harness\nprint('  ok  nothing said about the end')\n"
EXIT_ZERO_TEST = "import sys\nimport _harness\nprint('  ok  plain')\nsys.exit(0)\n"
CRASH_TEST = "import _harness\nraise ValueError('boom')\n"
NO_MARKER_TEST = CASE_TEST.split("# case: ")[0] + "print('  ok  not a case')\n"
NOT_KNOWN = "the test did not end through sys.exit or run_cases, so its exit code is not known"


def new_repo():
    """A git repository holding copies of evidence.py and _harness.py and two tests: one split into cases (one case fails), one plain (fails)"""
    root = Path(tempfile.mkdtemp()) / "target"
    (root / "scripts").mkdir(parents=True)
    (root / "tests").mkdir()
    shutil.copy(HERE.parent / "scripts" / "evidence.py", root / "scripts" / "evidence.py")
    shutil.copy(HERE / "_harness.py", root / "tests" / "_harness.py")
    (root / "tests" / "cases_test.py").write_text(CASE_TEST, encoding="utf-8")
    (root / "tests" / "plain_test.py").write_text(PLAIN_TEST, encoding="utf-8")
    for name, text in (("bare_fail_test.py", BARE_FAIL_TEST), ("bare_zero_test.py", BARE_ZERO_TEST), ("fall_off_test.py", FALL_OFF_TEST),
                       ("exit_zero_test.py", EXIT_ZERO_TEST), ("crash_test.py", CRASH_TEST), ("no_marker_test.py", NO_MARKER_TEST)):
        (root / "tests" / name).write_text(text, encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "init", "-q"], capture_output=True, check=True)
    return root


def run_test(root, name, *args):
    """(exit code, output, the evidence lines this run added)"""
    path = root / ".oqa" / "evidence.jsonl"
    before = len(path.read_text(encoding="utf-8").splitlines()) if path.is_file() else 0
    done = subprocess.run([sys.executable, str(root / "tests" / name)] + list(args), capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(root))
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()[before:]] if path.is_file() else []
    return done.returncode, done.stdout + done.stderr, lines


print("_harness.py — the evidence line says what happened")
repo = new_repo()
code, out, added = run_test(repo, "plain_test.py")
check("a plain test that fails (sys.exit(1)) is recorded as fail", code == 1 and [(e["result"], e["name"]) for e in added] == [("fail", "plain_test")], (code, added))
code, out, added = run_test(repo, "cases_test.py")
check("a case-based test with a failing case exits 1 and is recorded as fail — the ending `raise SystemExit(run_cases(...))` does not go through sys.exit",
      code == 1 and [(e["result"], e["name"], e["basis"]) for e in added] == [("fail", "cases_test", "exit code 1")], (code, added))
code, out, added = run_test(repo, "cases_test.py", "--case", "good")
check("only the passing case picked — exit 0 · recorded as pass under the case name", code == 0 and [(e["result"], e["name"]) for e in added] == [("pass", "cases_test:good")], (code, added))
code, out, added = run_test(repo, "cases_test.py", "--case", "bad")
check("only the failing case picked — exit 1 · recorded as fail under the case name", code == 1 and [(e["result"], e["name"]) for e in added] == [("fail", "cases_test:bad")], (code, added))
code, out, added = run_test(repo, "cases_test.py", "--list-cases")
check("--list-cases runs no case — the names are printed and no evidence line is left (nothing ran, so nothing passed)",
      code == 0 and "good" in out and "bad" in out and added == [], (code, out, added))
code, out, added = run_test(repo, "cases_test.py", "--case", "nosuch")
check("an unknown case name — exit 2 · no evidence line (nothing ran)", code == 2 and added == [], (code, added))
code, out, added = run_test(repo, "cases_test.py", "--case")
check("--case without a name — exit 2 · no evidence line", code == 2 and added == [], (code, added))

print("_harness.py — an ending the harness did not see is unknown, never a pass")
code, out, added = run_test(repo, "bare_fail_test.py")
check("a test that ends with a bare `raise SystemExit(1)` exits 1 and is recorded as unknown — nothing told the harness its exit code, so it is not a pass",
      code == 1 and [(e["result"], e["name"], e["basis"]) for e in added] == [("unknown", "bare_fail_test", NOT_KNOWN)], (code, added))
code, out, added = run_test(repo, "bare_zero_test.py")
check("a bare `raise SystemExit(0)` is unknown too (the harness cannot tell it from the 1 above)",
      code == 0 and [(e["result"], e["basis"]) for e in added] == [("unknown", NOT_KNOWN)], (code, added))
code, out, added = run_test(repo, "fall_off_test.py")
check("a test that runs off its end without sys.exit or run_cases is recorded as unknown, with the reason as basis",
      code == 0 and [(e["result"], e["name"], e["basis"]) for e in added] == [("unknown", "fall_off_test", NOT_KNOWN)], (code, added))
code, out, added = run_test(repo, "exit_zero_test.py")
check("sys.exit(0) is the known ending: recorded as pass with `exit code 0`",
      code == 0 and [(e["result"], e["name"], e["basis"]) for e in added] == [("pass", "exit_zero_test", "exit code 0")], (code, added))
code, out, added = run_test(repo, "crash_test.py")
check("an uncaught exception is known through the exception hook: recorded as fail with `exit code 1`",
      code == 1 and [(e["result"], e["name"], e["basis"]) for e in added] == [("fail", "crash_test", "exit code 1")], (code, added))
code, out, added = run_test(repo, "no_marker_test.py")
check("run_cases in a file without any `# case:` marker — exit 2 with a message · no evidence line (no case piece ran, so nothing passed)",
      code == 2 and "no `# case: ` marker" in out and "failures:" not in out and added == [], (code, out, added))
code, out, added = run_test(repo, "no_marker_test.py", "--list-cases")
check("--list-cases there still answers with an empty list · exit 0 · no evidence line", code == 0 and out.split() == ["#", "case", "list"] and added == [], (code, out, added))

print("\nfailures: %d" % len(failures))
sys.exit(1 if failures else 0)
