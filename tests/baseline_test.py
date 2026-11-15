# -*- coding: utf-8 -*-
"""baseline.py — when there is no map it records first, a function no test passes through shows up in the list, the file with weak tests
comes near the top of the risk map, and a cell that could not be measured is "not measured" rather than 0.
`python tests/baseline_test.py`"""
import _harness  # noqa: F401,E402 — collects the temp folders in one place for this test and removes them at the end (first)
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
scripts = Path(__file__).resolve().parents[1] / "scripts"
failures = []


def check(name, condition, text=""):
    print(("  ok  " if condition else "  FAIL  ") + name)
    if not condition:
        failures.append(name)
        if text:
            print("        " + str(text)[:500].replace("\n", " / "))


def run(*args):
    done = subprocess.run([sys.executable, str(scripts / "baseline.py")] + [str(a) for a in args], capture_output=True,
                          env=dict(os.environ, PYTHONIOENCODING="utf-8"))
    return done.returncode, done.stdout.decode("utf-8", "replace") + done.stderr.decode("utf-8", "replace")


def git(repo_, *command):
    subprocess.run(["git", "-C", str(repo_)] + list(command), capture_output=True, check=True)


def commit(repo_, message):
    git(repo_, "add", "-A")
    git(repo_, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", message)


path_head = "import os, sys\nsys.path.insert(0, os.getcwd())\n"
# (1) strong.sign — the test looks at every branch and boundary (every mutant is killed)
strong = ('"""module that gets strong tests"""\n\n\ndef sign(x):\n    """sign"""\n    if x > 0:\n        return 1\n    if x < 0:\n        return -1\n    return 0\n')
# (2) weak.discount — the test only calls it and does not look at the value (mutants survive)
weak = ("def discount(value, rate):\n    if rate > 0.5:\n        rate = 0.5\n    cut = value * rate\n    return value - cut\n")
# (3) big.unused — no test passes through it / (4) big.Calculator.big_sum — a big function (a test passes through and looks at one result only)
big = ("def unused(x):\n    y = x + 1\n    return y * 2\n\n\nclass Calculator:\n    def big_sum(self, n):\n" +
       "".join("        n = n + %d\n" % i for i in range(1, 21)) + "        if n > 1000:\n            return 0\n        return n\n")
test_files = {
    "strong": ("tests/test_strong.py", path_head + "import strong\nok = [strong.sign(5), strong.sign(1), strong.sign(-5), strong.sign(-1), strong.sign(0)] == [1, 1, -1, -1, 0]\n"
                                                   "print('  ok' if ok else '  FAIL  strong')\n"),
    "weak": ("weak_test.py", path_head + "import weak\nweak.discount(100, 0.1)\nprint('  ok')\n"),
    "big": ("checks/big_check.py", path_head + "import big\nprint('  ok' if big.Calculator().big_sum(0) == 210 else '  FAIL  big')\n"),
}
root = Path(tempfile.mkdtemp())
repo = root / "repo"
for f, text in (("strong.py", strong), ("weak.py", weak), ("big.py", big), ("tests/helper.py", "def helping():\n    return 1\n"),
                ("test_root.py", "def root_test():\n    return 1\n")):
    (repo / f).parent.mkdir(parents=True, exist_ok=True)
    (repo / f).write_text(text, encoding="utf-8")
for name, (f, text) in test_files.items():
    (repo / f).parent.mkdir(parents=True, exist_ok=True)
    (repo / f).write_text(text, encoding="utf-8")
(repo / ".oqa").mkdir()
(repo / ".oqa" / "tests.json").write_text(json.dumps([{"name": name, "command": "python " + f} for name, (f, _) in test_files.items()], ensure_ascii=False),
                                          encoding="utf-8")
git(repo, "init", "-q")
commit(repo, "first")


def tracked_hashes(repo_):
    files = subprocess.run(["git", "-C", str(repo_), "-c", "core.quotepath=false", "ls-files", "-z"], capture_output=True).stdout.decode("utf-8").split("\0")
    return {f: hashlib.sha1((repo_ / f).read_bytes()).hexdigest() for f in files if f}


original_hashes = tracked_hashes(repo)
map_path, result_path = repo / ".oqa" / "impact-map.json", repo / ".oqa" / "baseline.json"

print("── E1 baseline: no map → record · mutation sample ──")
check("before the start there is no impact map", not map_path.exists())
code, text = run("--repo", repo, "--sample", "6")
check("exit code 0 · there was no map so it recorded first (impact-map.json appears)", code == 0 and map_path.is_file() and "recording a new one" in text, text[-600:])
r = json.loads(result_path.read_text(encoding="utf-8")) if result_path.is_file() else {}
check("baseline.json — created · HEAD (the HEAD now) · map_created · by_file · by_function · risk_ranking",
      bool(r.get("created")) and r.get("HEAD") == subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
      and bool(r.get("map_created")) and r.get("by_file") and r.get("by_function") and r.get("risk_ranking") and r.get("map_recorded") is True, list(r))
files = {d["file"]: d for d in r.get("by_file", [])}
functions = {(d["file"], d["function"]): d for d in r.get("by_function", [])}
check("the targets are the three strong · weak · big — tests/ · test_*.py · *_test.py · the file in a tests.json command (checks/big_check.py) are left out", set(files) == {"strong.py", "weak.py", "big.py"}, sorted(files))
unused = functions.get(("big.py", "unused"), {})
check("(3) big.unused — in the list of functions no test passes through, coverage 0 · tests through 0 · executable lines 2",
      {"file": "big.py", "function": "unused", "executable_lines": 2} in r.get("untested_functions", []) and unused.get("coverage") == 0 and unused.get("tests_through") == 0
      and unused.get("executable_lines") == 2, (unused, r.get("untested_functions")))
untested_names = {(d["file"], d["function"]) for d in r.get("untested_functions", [])}
check("functions a test passes through (sign · discount · Calculator.big_sum) are not in the untested list · the name of big_sum is Class.function",
      untested_names == {("big.py", "unused")} and ("big.py", "Calculator.big_sum") in functions, sorted(untested_names))
sign = functions.get(("strong.py", "sign"), {})
check("(1) strong.sign — executable lines 5 (docstring · def header excluded) · coverage 100% · tests through 1", sign.get("executable_lines") == 5 and sign.get("coverage") == 1.0 and sign.get("tests_through") == 1, sign)
discount = functions.get(("weak.py", "discount"), {})
check("(2) weak.discount — there is coverage (a test passes through) but the rate > 0.5 branch is not passed, so coverage < 100%", discount.get("tests_through") == 1 and 0 < (discount.get("coverage") or 0) < 1, discount)
check("file coverage — big.py is below 100% because of unused · tests through 1", 0 < (files.get("big.py", {}).get("coverage") or 0) < 1 and files["big.py"]["tests_through"] == 1, files.get("big.py"))
# ending with an error counts as killed: the mutant that removes the line `cut = value * rate` of weak.discount is killed by a NameError even in a test that does not look at the value (1 / 4). The three mutants that change the value survive
check("(1) strong.py mutation measured · killed 100% / (2) weak.py measured · killed 25% (only the one that removes the assignment line is killed by an error — the three that change the value survive)", files.get("strong.py", {}).get("mutation") == "measured" and files["strong.py"].get("kill_rate") == 1.0
      and files.get("weak.py", {}).get("mutation") == "measured" and files["weak.py"].get("kill_rate") == 0.25 and files["weak.py"]["counts"]["survived"] == 3, (files.get("strong.py"), files.get("weak.py")))
check("(2) weak.py ranks above (1) strong.py in risk (files) · for functions too weak.discount is above strong.sign",
      files.get("weak.py", {}).get("rank", 99) < files.get("strong.py", {}).get("rank", 0) and discount.get("rank", 99) < sign.get("rank", 0),
      [(d["file"], d["rank"], d["score"]) for d in r.get("by_file", [])])
check("score = uncovered lines + covered lines × (1 − kill rate) — strong.py is 0 (all covered and all killed)", files.get("strong.py", {}).get("score") == 0, files.get("strong.py"))
check("risk_ranking is in the same order as the ranks of by_function", [d["rank"] for d in r.get("risk_ranking", [])] == list(range(1, len(r.get("by_function", [])) + 1)))
check("standard output has the risk map tables · the line of untested functions", "risk map — files" in text and "risk map — functions" in text and "big.py · unused" in text, text[-600:])
check("the tracked files of the original repository are unchanged (hashes)", tracked_hashes(repo) == original_hashes)

print("── --no-mutation · a usable map is used as it is ──")
map_time = map_path.stat().st_mtime_ns
code, text2 = run("--repo", repo, "--no-mutation")
r2 = json.loads(result_path.read_text(encoding="utf-8"))
check("exit code 0 · the map was not recorded again (usable)", code == 0 and map_path.stat().st_mtime_ns == map_time and r2.get("map_recorded") is False, text2[-400:])
check("the mutation cells are None (\"not measured\") — not 0 · a row with covered lines has score None too", all(d["kill_rate"] is None and d["counts"] is None
      and d["mutation"].startswith("not measured") and (d["score"] is None) == (d["covered_lines"] > 0) for d in r2["by_file"] + r2["by_function"]), r2["by_file"])
functions2 = {d["function"]: d for d in r2["by_function"]}
check("big.unused, which no test passes through, has score = executable lines 2 even without a kill rate (0 covered lines — second term 0) · function rank 1 (rows with a score are on top)",
      functions2.get("unused", {}).get("score") == 2.0 and functions2["unused"].get("rank") == 1, functions2.get("unused"))
check("among rows without a kill rate, more uncovered lines first — big.py (2 lines of unused not passed) is above strong.py (0 lines)",
      {d["file"]: d["rank"] for d in r2["by_file"]}.get("big.py", 99) < {d["file"]: d["rank"] for d in r2["by_file"]}.get("strong.py", 0), r2["by_file"])
check("the table prints \"not measured\"", "not measured" in text2)
check("the progress line of each file gives the state once — `killed not measured (--no-mutation)`, not `killed not measured (not measured (--no-mutation))` · a measured file reads `killed 100% (measured)`",
      "not measured (not measured" not in text + text2 and "· killed not measured (--no-mutation)\n" in text2.replace("\r\n", "\n") and "· killed 100% (measured)\n" in text.replace("\r\n", "\n"), text2[:500])
code, text3 = run("--repo", repo, "--no-mutation", "--files", "strong.py", "tests")
r3 = json.loads(result_path.read_text(encoding="utf-8"))
check("--files strong.py tests → the only target is strong.py (test files inside the folder are left out)", code == 0 and [d["file"] for d in r3["by_file"]] == ["strong.py"], text3[-300:])

print("── stale map → record again ──")
(repo / "README.txt").write_text("second\n", encoding="utf-8")
commit(repo, "second")
code, text4 = run("--repo", repo, "--no-mutation")
r4 = json.loads(result_path.read_text(encoding="utf-8"))
check("when HEAD changes the map is stale → recorded again (map_HEAD = the HEAD now)", code == 0 and r4.get("map_recorded") is True and r4.get("map_HEAD") == r4.get("HEAD")
      and "is stale (" in text4, text4[-300:])
evidence_lines = [json.loads(line) for line in (repo / ".oqa" / "evidence.jsonl").read_text(encoding="utf-8").splitlines()] if (repo / ".oqa" / "evidence.jsonl").is_file() else []
check("when baseline finishes, that repository's evidence gets one Baseline pass line (with the fingerprint)", bool(evidence_lines) and evidence_lines[-1]["kind"] == "Baseline" and evidence_lines[-1]["result"] == "pass"
      and evidence_lines[-1]["fingerprint"] not in ("", "unknown") and evidence_lines[-1]["tool"] == "baseline.py", evidence_lines[-1:])

print("── a repository with a test whose baseline fails mixed in ──")
repo2 = root / "broken_repo"
shutil.copytree(repo, repo2, ignore=shutil.ignore_patterns("impact-map.json", "baseline.json"))
(repo2 / "tests" / "test_broken.py").write_text(path_head + "import weak\nweak.discount(10, 0.9)\nprint('  FAIL  a test that was already broken')\n", encoding="utf-8")
test_entries = json.loads((repo2 / ".oqa" / "tests.json").read_text(encoding="utf-8")) + [{"name": "broken", "command": "python tests/test_broken.py"}]
(repo2 / ".oqa" / "tests.json").write_text(json.dumps(test_entries, ensure_ascii=False), encoding="utf-8")
commit(repo2, "broken test")
code, text5 = run("--repo", repo2, "--sample", "4")
r5 = json.loads((repo2 / ".oqa" / "baseline.json").read_text(encoding="utf-8")) if (repo2 / ".oqa" / "baseline.json").is_file() else {"by_file": []}
files5 = {d["file"]: d for d in r5["by_file"]}
check("weak.py, which the broken test passes through, is \"not measured (baseline failed)\" · kill rate None", files5.get("weak.py", {}).get("mutation") == "not measured (baseline failed)"
      and files5["weak.py"].get("kill_rate") is None, (files5.get("weak.py"), text5[-400:]))
evidence5 = [json.loads(line) for line in (repo2 / ".oqa" / "evidence.jsonl").read_text(encoding="utf-8").splitlines() if "\"Baseline\"" in line]
check("a test that fails while the impact map is recorded: the baseline says so, exits with 1 and records the Baseline evidence as `fail` naming the test — rule changed: the exit code of the recording was ignored (exit code 0 · `pass`)",
      code == 1 and "1 test(s) failed while the impact map was recorded: broken" in text5 and r5.get("failed_tests") == ["broken"]
      and [(d["result"], "broken" in d["basis"]) for d in evidence5[-1:]] == [("fail", True)], (code, text5[-500:], evidence5[-1:]))
check("strong.py, which the broken test does not pass through, is measured as before", files5.get("strong.py", {}).get("mutation") == "measured", files5.get("strong.py"))
check("the function rows of weak.py are \"not measured (baseline failed)\" too", all(d["mutation"] == "not measured (baseline failed)" for d in r5.get("by_function", []) if d["file"] == "weak.py")
      and any(d["file"] == "weak.py" for d in r5.get("by_function", [])))

print("── exit code 2 when the map cannot be used ──")
repo3 = root / "no_list"
repo3.mkdir()
(repo3 / "a.py").write_text("def a():\n    return 1\n", encoding="utf-8")
git(repo3, "init", "-q")
commit(repo3, "first")
code, text6 = run("--repo", repo3, "--no-mutation")
check("without a test list (.oqa/tests.json) exit code 2 · baseline.json is not made", code == 2 and not (repo3 / ".oqa" / "baseline.json").exists(), text6[-300:])

print("── kill rate · score formula ──")
sys.path.insert(0, str(scripts))
import baseline  # noqa: E402
_, rate = baseline.kill_counts([{"verdict": v} for v in ("killed", "survived", "crashed", "crashed", "gap")])
check("kill rate = killed / (killed + survived) — crashed · gap are not in the denominator (1/2)", rate == 0.5, rate)
check("with only crashed · gap the kill rate is None (not 0)", baseline.kill_counts([{"verdict": "crashed"}, {"verdict": "gap"}])[1] is None)
check("score(10 lines · covered 4 · killed 50%) = 6 + 4 × 0.5 = 8 · with kill rate None the score is None", baseline.score(10, 4, 0.5) == 8.0 and baseline.score(10, 4, None) is None)
check("with 0 covered lines the score = number of lines (10) even when the kill rate is None", baseline.score(10, 0, None) == 10.0)
check("a row without any executable line and without a measured kill rate has no score (None) — it used to show 0.0, which reads as \"no weak line\" · with a measured kill rate it is 0.0",
      baseline.score(0, 0, None) is None and baseline.score(0, 0, 0.5) == 0.0, (baseline.score(0, 0, None), baseline.score(0, 0, 0.5)))

print("── stops when recording again failed and the old map is still there ──")
import subprocess as _sp  # noqa: E402
repo5 = root / "old_map"
repo5.mkdir()
(repo5 / "a.py").write_text("def a():\n    return 1\n", encoding="utf-8")
(repo5 / ".oqa").mkdir()
(repo5 / ".oqa" / "tests.json").write_text('[{"name": "a", "command": "python -c pass"}]', encoding="utf-8")
for command in (["init", "-q"], ["add", "-A"], ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "first"]):
    _sp.run(["git", "-C", str(repo5)] + command, capture_output=True, check=True)
(repo5 / ".oqa" / "impact-map.json").write_text('{"created": "2026-01-01 00:00", "HEAD": "oldHEAD", "modified": [], "tests": {"a": {"a.py": [1, 2]}}, "results": {"a": "pass"}}', encoding="utf-8")
original_record = baseline.impact.record
baseline.impact.record = lambda *a, **k: 0            # recording that changed nothing (the old map is still there)
try:
    import contextlib as _cl, io as _io  # noqa: E401,E402
    captured = _io.StringIO()
    with _cl.redirect_stdout(captured):
        code5 = baseline.main(["--repo", str(repo5), "--no-mutation"])
finally:
    baseline.impact.record = original_record
check("when recording does not write the map file anew (old map still there) exit code 2 · baseline.json is not made (it does not measure with old lines)",
      code5 == 2 and not (repo5 / ".oqa" / "baseline.json").exists() and "could not record the impact map" in captured.getvalue(), (code5, captured.getvalue()[-200:]))
# an old map with the same HEAD that is stale because it was "recorded with modified .py files" + recording fails — a HEAD check cannot filter this out
head_now = _sp.run(["git", "-C", str(repo5), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
(repo5 / ".oqa" / "impact-map.json").write_text(json.dumps({"created": "2026-01-01 00:00", "HEAD": head_now, "modified": ["a.py"],
                                                            "tests": {"a": {"a.py": [1, 2]}}, "results": {"a": "pass"}}, ensure_ascii=False), encoding="utf-8")
baseline.impact.record = lambda *a, **k: 0
try:
    captured = _io.StringIO()
    with _cl.redirect_stdout(captured):
        code6 = baseline.main(["--repo", str(repo5), "--no-mutation"])
finally:
    baseline.impact.record = original_record
check("when recording again leaves the map file unchanged, an old map is not used even if its HEAD is the HEAD now — exit code 2 · baseline.json is not made",
      code6 == 2 and not (repo5 / ".oqa" / "baseline.json").exists(), (code6, captured.getvalue()[-200:]))
# what prepare_map returns then — no map · recording was attempted · the reason (main stops at map None and never looks at these values, so a mutant flipping "recorded" survived)
baseline.impact.record = lambda *a, **k: 0
try:
    with _cl.redirect_stdout(_io.StringIO()):
        prepared = baseline.prepare_map(repo5, None)
finally:
    baseline.impact.record = original_record
check("prepare_map — when recording again does not change the map file: (None, recording attempted True, \"could not record again\")", prepared == (None, True, "could not record again"), prepared)
# when recording did write the map file anew but its HEAD differs from now (the commit changed during recording) it stops — no test used to pass through this line
def _write_with_other_head(repo_, fail_pattern_):
    (Path(repo_) / ".oqa" / "impact-map.json").write_text(json.dumps({"created": "2099-01-01 18:20", "HEAD": "otherHEAD", "modified": [],
                                                                      "tests": {"a": {"a.py": [1, 2]}}, "results": {"a": "pass"}}, ensure_ascii=False), encoding="utf-8")
    return 0
import time as _t0  # noqa: E402
_t0.sleep(0.05)
(repo5 / ".oqa" / "baseline.json").unlink(missing_ok=True)
baseline.impact.record = _write_with_other_head
try:
    captured = _io.StringIO()
    with _cl.redirect_stdout(captured):
        code8 = baseline.main(["--repo", str(repo5), "--no-mutation"])
finally:
    baseline.impact.record = original_record
check("when the HEAD of the newly written map differs from now exit code 2 · \"differs from the current HEAD\" · baseline.json is not made",
      code8 == 2 and "differs from the current HEAD" in captured.getvalue() and not (repo5 / ".oqa" / "baseline.json").exists(), (code8, captured.getvalue()[-200:]))
# when recording really writes anew (even if "stale" because it was recorded with modified files) that map is used — those lines were just measured with those files
def _write_anew(repo_, fail_pattern_):
    (Path(repo_) / ".oqa" / "impact-map.json").write_text(json.dumps({"created": "2099-01-01 18:00", "HEAD": head_now, "modified": ["a.py"],
                                                                      "tests": {"a": {"a.py": [1, 2]}}, "results": {"a": "pass"}}, ensure_ascii=False), encoding="utf-8")
    return 0
import time as _t  # noqa: E402
_t.sleep(0.05)
baseline.impact.record = _write_anew
try:
    with _cl.redirect_stdout(_io.StringIO()):
        code7 = baseline.main(["--repo", str(repo5), "--no-mutation"])
finally:
    baseline.impact.record = original_record
check("when recording wrote the map anew (even if stale from recording with modified files) that map is used — exit code 0 · baseline.json is made",
      code7 == 0 and (repo5 / ".oqa" / "baseline.json").is_file(), code7)

# when the code changes while measuring (start and end fingerprints differ) the fingerprint of the Baseline evidence is "unknown" — the end fingerprint is not recorded
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import evidence as _ev  # noqa: E402
alternating, original_fingerprint = iter(["fingerprint_before", "fingerprint_after"]), _ev.fingerprint
_ev.fingerprint = lambda *a, **k: next(alternating)
_t.sleep(0.05)
baseline.impact.record = _write_anew
try:
    captured = _io.StringIO()
    with _cl.redirect_stdout(captured):
        code9 = baseline.main(["--repo", str(repo5), "--no-mutation"])
finally:
    baseline.impact.record, _ev.fingerprint = original_record, original_fingerprint
last_line = json.loads((repo5 / ".oqa" / "evidence.jsonl").read_text(encoding="utf-8").splitlines()[-1])
check("when the code changes while baseline is measuring, the fingerprint of the Baseline evidence is \"unknown\"", code9 == 0 and last_line["kind"] == "Baseline" and last_line["fingerprint"] == "unknown", last_line)
check("… and its result is `unknown` too, never `pass` — a pass that belongs to no code is not evidence (it used to be recorded as `pass`) · the run says so",
      last_line["result"] == "unknown" and "the code changed while the baseline was measured" in last_line["basis"] and "Baseline evidence: unknown" in captured.getvalue(), (last_line, captured.getvalue()[-300:]))
_ev.fingerprint = lambda *a, **k: None
_t.sleep(0.05)
baseline.impact.record = _write_anew
try:
    with _cl.redirect_stdout(_io.StringIO()):
        code10 = baseline.main(["--repo", str(repo5), "--no-mutation"])
finally:
    baseline.impact.record, _ev.fingerprint = original_record, original_fingerprint
last_line = json.loads((repo5 / ".oqa" / "evidence.jsonl").read_text(encoding="utf-8").splitlines()[-1])
check("when the fingerprint cannot be measured at all the Baseline evidence is `unknown` with the fingerprint \"unknown\" as well",
      code10 == 0 and (last_line["result"], last_line["fingerprint"]) == ("unknown", "unknown") and "could not be measured" in last_line["basis"], last_line)

shutil.rmtree(root, ignore_errors=True)
print("\nfailures: %d" % len(failures))
sys.exit(1 if failures else 0)
