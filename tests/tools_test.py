# -*- coding: utf-8 -*-
"""**Checks that the verification tools (mutation · impact tests · requirement tracing · verification report) count as promised.**

    python tests/tools_test.py

Builds small git repositories with a deliberately weak test and a strong test, and compares the numbers the tools give with answers fixed in advance (both what must be counted and what must not).
"""
import _harness  # noqa: F401,E402 — collects the temp folders in one place for this test and removes it at the end (first — before another import reads tempdir)
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

for _key in ("PYTHONIOENCODING", "PYTHONUTF8"):
    os.environ.pop(_key, None)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
TOOLS = Path(__file__).resolve().parents[1] / "scripts"
failures = []


def check(name, condition, text=""):
    print(("  ok  " if condition else "  FAIL  ") + name)
    if not condition:
        failures.append(name)
        if text:
            print("        " + str(text)[:500].replace("\n", " / "))


def git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd)] + list(args), capture_output=True, check=True)


def run_tool(name, *args, cwd=None):
    done = subprocess.run([sys.executable, str(TOOLS / name)] + [str(a) for a in args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", cwd=cwd)
    return done.returncode, done.stdout + done.stderr


def make_repo():
    root = Path(tempfile.mkdtemp()) / "target"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.email", "t@t")
    git(root, "config", "user.name", "t")
    (root / "calc.py").write_text("def grade(score):\n    return 'accepted' if score >= 60 else 'refused'\n", encoding="utf-8")
    (root / "strongtest.py").write_text(
        "import sys\nsys.path.insert(0, '.')\nfrom calc import grade\n"
        "for score, answer in ((59, 'refused'), (60, 'accepted'), (61, 'accepted')):\n"
        "    print(('  ok  ' if grade(score) == answer else '  FAIL  ') + str(score))\n", encoding="utf-8")
    (root / "weaktest.py").write_text(
        "import sys\nsys.path.insert(0, '.')\nfrom calc import grade\n"
        "print(('  ok  ' if grade(90) == 'accepted' else '  FAIL  ') + '90')\n", encoding="utf-8")
    (root / "haltingtest.py").write_text("raise SystemExit(3)\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "first")
    return root


print("mutate.py")
mut = make_repo()
result_path = mut.parent / "result.json"
code, text = run_tool("mutate.py", "--repo", mut, "--files", "calc.py", "--range", "all", "--test", "python strongtest.py", "--json", result_path)
d = json.loads(result_path.read_text(encoding="utf-8")) if result_path.is_file() else {}
kinds = {m["kind"] for m in d.get("mutants", [])}
check("builds the boundary comparison (>= → >)", any("GtE → Gt" in k for k in kinds), kinds)
check("the strong test kills the boundary mutant", any(m["kind"].startswith("compare GtE") and m["verdict"] == "killed" for m in d.get("mutants", [])), d)
check("with the strong test survived 0 · exit code 0", code == 0 and d.get("counts", {}).get("survived") == 0, text)
check("the original file is untouched", (mut / "calc.py").read_text(encoding="utf-8").count(">= 60") == 1)

code, text = run_tool("mutate.py", "--repo", mut, "--files", "calc.py", "--range", "all", "--test", "python weaktest.py", "--json", result_path)
d = json.loads(result_path.read_text(encoding="utf-8"))
check("the weak test does not kill the boundary mutant → counted as survived", any(m["kind"].startswith("compare GtE") and m["verdict"] == "survived" for m in d["mutants"]), d)
check("exit code 1 when something survived", code == 1, text)

code, text = run_tool("mutate.py", "--repo", mut, "--files", "calc.py", "--range", "all", "--test", "python haltingtest.py")
check("a test that already fails on the baseline (original) is not counted and it stops (exit code 2)", code == 2 and "baseline" in text, text)

# crashed: a mutant that makes the test die without a failure line
(mut / "divide.py").write_text("def divide(a, b):\n    return a / b\n", encoding="utf-8")
(mut / "dividetest.py").write_text("import sys\nsys.path.insert(0, '.')\nfrom divide import divide\nassert divide(4, 2) == 2\nprint('end')\n", encoding="utf-8")
code, text = run_tool("mutate.py", "--repo", mut, "--files", "divide.py", "--range", "all", "--test", "python dividetest.py", "--json", result_path)
d = json.loads(result_path.read_text(encoding="utf-8"))
# The original passes and only the mutant ends with an error (exit code ≠ 0), so the mutant is to blame → killed.
# If this were counted as "crashed", every mutant that removes an assignment (always a NameError) would run its tests to the end and still come out as "cannot judge"
check("ending with an error without a failure mark (assert · exception — exit code ≠ 0) is killed · the basis has \"ended with an error\" and the last line", d["counts"]["killed"] >= 1 and d["counts"]["crashed"] == 0
      and all("ended with an error" in (m.get("basis") or "") for m in d["mutants"] if m["verdict"] == "killed"), d)
(mut / "notify.py").write_text("def notify(x):\n    print('notice', x)\n    kept.append(x)\n    return x + 1\n\n\nkept = []\n", encoding="utf-8")
(mut / "notifytest.py").write_text("import sys\nsys.path.insert(0, '.')\nimport notify\nprint('  ok  a' if notify.notify(1) == 2 and notify.kept == [1] else '  FAIL  a')\n", encoding="utf-8")
code, text = run_tool("mutate.py", "--repo", mut, "--files", "notify.py", "--range", "all", "--test", "python notifytest.py", "--json", result_path)
d = json.loads(result_path.read_text(encoding="utf-8"))
check("no mutant removes a line that is only a print (0 mutants on line 2) · a line that calls another function (line 3 — kept.append) is removed",
      not [m for m in d["mutants"] if m["line"] == 2] and any(m["line"] == 3 and m["kind"] == "statement → pass" and m["verdict"] == "killed" for m in d["mutants"]), d["mutants"])

# a mutant that makes the test hang (timeout) is not killed either
(mut / "hangtarget.py").write_text("def value():\n    return 1\n", encoding="utf-8")
(mut / "hangtest.py").write_text("import sys, time\nsys.path.insert(0, '.')\nfrom hangtarget import value\nif value() != 1:\n    time.sleep(30)\nprint('  ok  end')\n", encoding="utf-8")
code, text = run_tool("mutate.py", "--repo", mut, "--files", "hangtarget.py", "--range", "all", "--test", "python hangtest.py", "--timeout", "3", "--json", result_path)
d = json.loads(result_path.read_text(encoding="utf-8"))
check("a mutant that makes the test hang (timeout) is \"crashed\" — killed 0", d["counts"]["crashed"] >= 1 and d["counts"]["killed"] == 0, d)

# changed lines only
(mut / "calc.py").write_text("def grade(score):\n    return 'accepted' if score >= 60 else 'refused'\n\n\ndef double(x):\n    return x * 2\n", encoding="utf-8")
code, text = run_tool("mutate.py", "--repo", mut, "--files", "calc.py", "--test", "python strongtest.py", "--json", result_path)
d = json.loads(result_path.read_text(encoding="utf-8"))
lines = {m["line"] for m in d["mutants"]}
check("the working range mutates only the changed lines (5~6)", lines and lines <= {5, 6}, lines)
check("a mutant of a changed line the strong test does not kill is survived (the double function has no test)", d["counts"]["survived"] >= 1, d)
git(mut, "checkout", "--", "calc.py")
code, text = run_tool("mutate.py", "--repo", mut, "--files", "calc.py", "--test", "python strongtest.py", "--json", result_path)
check("without changed lines it says \"0 mutants generated\" — 0 is not reported as \"no weak tests\"", "0 mutants generated" in text and "does not mean" in text, text)

# cutting by column (in bytes) on a line with non-ASCII text
(mut / "accent.py").write_text("def judge(value):\n    memo = 'été über 中文'; return value > 3\n", encoding="utf-8")
(mut / "accenttest.py").write_text("import sys\nsys.path.insert(0, '.')\nfrom accent import judge\nprint(('  ok  ' if judge(3) is False and judge(4) is True else '  FAIL  ') + 'boundary')\n", encoding="utf-8")
code, text = run_tool("mutate.py", "--repo", mut, "--files", "accent.py", "--range", "all", "--test", "python accenttest.py", "--json", result_path)
d = json.loads(result_path.read_text(encoding="utf-8"))
check("on a line with non-ASCII text in front the compare mutant still goes in at the right place and is killed", any(m["kind"] == "compare Gt → GtE" and m["verdict"] == "killed" for m in d["mutants"]), d)


def mutate_json(*args):
    """(exit code, printed text, the --json result or {} when none was written)"""
    if result_path.is_file():
        result_path.unlink()
    code_, text_ = run_tool("mutate.py", "--repo", mut, *args, "--json", result_path)
    return code_, text_, (json.loads(result_path.read_text(encoding="utf-8")) if result_path.is_file() else {})


# every test the mutants use is run once, unmutated, in the copy — also when its result was given. The copy leaves out .git · .oqa · node_modules,
# so a test can pass in the repository and fail in the copy; it would then "fail" on every mutant and every mutant would be counted as killed
(mut / ".oqa").mkdir(exist_ok=True)
(mut / ".oqa" / "marker.txt").write_text("only in the repository\n", encoding="utf-8")
(mut / "envtest.py").write_text("import os, sys\nsys.path.insert(0, '.')\nfrom calc import grade\nhere = os.path.exists(os.path.join('.oqa', 'marker.txt'))\n"
                                "print(('  ok  ' if here and grade(60) == 'accepted' else '  FAIL  ') + 'needs the .oqa folder')\n", encoding="utf-8")
in_repository = subprocess.run([sys.executable, "envtest.py"], cwd=str(mut), capture_output=True, text=True, encoding="utf-8", errors="replace")
given_path = mut.parent / "given.json"
given_path.write_text(json.dumps({"python envtest.py": "pass", "python strongtest.py": "pass"}), encoding="utf-8")
code, text, d = mutate_json("--files", "calc.py", "--range", "all", "--test", "python envtest.py", "--baseline-results", given_path)
check("a test given as \"pass\" that passes in the repository but fails in the unmutated copy → baseline fail, exit code 2, no mutant counted (not: every mutant killed) · the basis says so and names the test · 1 test run",
      in_repository.returncode == 0 and "  ok  " in in_repository.stdout and code == 2 and d.get("baseline") == "fail" and d.get("mutants") == [] and "counts" not in d
      and str(d.get("basis")).startswith("passes in the repository but fails in the copy: python envtest.py — ") and "needs the .oqa folder" in d["basis"] and d.get("test_runs") == 1,
      (in_repository.stdout, code, d, text[-300:]))
code, text, d = mutate_json("--files", "calc.py", "--range", "all", "--test", "python envtest.py")
check("the same test without a given result fails the baseline too, and the basis does not claim that it passes in the repository (nobody saw that)",
      code == 2 and d.get("baseline") == "fail" and str(d.get("basis")).startswith("python envtest.py: ") and "passes in the repository" not in d["basis"], (code, d))
code, text, d = mutate_json("--files", "calc.py", "--range", "all", "--test", "python strongtest.py", "--baseline-results", given_path)
check("a given \"pass\" does not replace the run in the copy: the test is run once unmutated — test runs = runs on mutants + 1 · the result and the printed line say 1 test was run in the copy",
      code == 0 and d.get("baseline") == "pass" and d.get("baseline_runs") == 1 and len(d.get("mutants", [])) > 0 and d.get("test_runs") == sum(m["ran"] for m in d["mutants"]) + 1
      and "tests run unmutated in the copy 1 (results given 1)" in text, (code, d.get("baseline_runs"), d.get("test_runs"), text[:300]))
(mut / "envtest.py").unlink()

# --stop-over with tests that have no measured time: each such test gets the time measured when it ran unmutated in the copy,
# so the estimate is still made (a test added after the map was recorded must not make every report stop) and the limit still applies
times_path = mut.parent / "times.json"
both_tests = ("--files", "calc.py", "--range", "all", "--test", "python strongtest.py", "--test", "python weaktest.py", "--tests-file", times_path)
for label, seconds_of_weak in (("one of two selected tests has no measured time", {"seconds": 0.1}), ("no selected test has a measured time", {})):
    times_path.write_text(json.dumps([{"name": "strong", "command": "python strongtest.py"}, dict({"name": "weak", "command": "python weaktest.py"}, **seconds_of_weak)]), encoding="utf-8")
    code, text, d = mutate_json(*both_tests, "--stop-over", "100000")
    check("--stop-over · %s → the time measured in the copy is used: an estimate is given with no test left out, and the run goes ahead under the limit" % label,
          code in (0, 1) and "estimate for" in text and "no measured time" not in text and "cannot be estimated" not in text and "stopped" not in d and len(d.get("mutants", [])) > 0, (code, text[-400:]))
    code, text, d = mutate_json(*both_tests, "--stop-over", "0.000001")
    check("--stop-over · %s and a limit far below the measured time → stops on that estimate with exit code 4 · no mutant counted" % label,
          code == 4 and "estimate for" in d.get("stopped", "") and d.get("mutants") == [] and "counts" not in d and d.get("baseline") == "pass", (code, text[-400:], d))
code, text, d = mutate_json(*both_tests)
check("without --stop-over the same run goes ahead (mutants are counted)", code in (0, 1) and "stopped" not in d and len(d.get("mutants", [])) > 0 and d.get("counts"), (code, text[-300:]))

# --max below 1 is refused — a run of 0 mutants would read as "0 mutants generated"
code, text, d = mutate_json("--files", "calc.py", "--range", "all", "--test", "python strongtest.py", "--max", "0")
check("mutate.py --max 0 is refused by the argument parser (exit code 2, \"--max must be 1 or more\") and writes no result", code == 2 and "error:" in text and "--max must be 1 or more" in text and d == {}, (code, text[-300:], d))

# a target file that cannot be read as Python is a clean "not run", not a traceback
(mut / "latin.py").write_bytes(b"# -*- coding: latin-1 -*-\nname = 'caf\xe9'\n\n\ndef size():\n    return len(name) > 3\n")
(mut / "broken.py").write_text("def broken(:\n    return 1 > 0\n", encoding="utf-8")
for file, problem in (("latin.py", "is not valid UTF-8"), ("broken.py", "does not parse as Python")):
    for files in ((file,), ("calc.py", file)):
        code, text, d = mutate_json("--files", *files, "--range", "all", "--test", "python strongtest.py")
        check("a target file that %s (%s) → exit code 2 · no traceback · baseline \"not run\" · the basis names the file and the problem · no mutant counted" % (problem, " + ".join(files)),
              code == 2 and "Traceback" not in text and d.get("baseline") == "not run" and d.get("mutants") == [] and "counts" not in d and d.get("test_runs") == 0
              and file in str(d.get("basis")) and problem in str(d.get("basis")) and file in text, (code, text[-300:], d))
(mut / "latin.py").unlink()
(mut / "broken.py").unlink()

# how many mutants each target file gave — a file with 0 among files with some must be visible to the report
(mut / "headers.py").write_text("def nothing(x):\n    pass\n", encoding="utf-8")
code, text, d = mutate_json("--files", "calc.py", "headers.py", "--range", "all", "--test", "python strongtest.py")
check("the result counts the generated mutants per target file — a file that gave none is listed with 0", d.get("generated_by_file", {}).get("headers.py") == 0
      and d.get("generated_by_file", {}).get("calc.py", 0) > 0 and sum(d.get("generated_by_file", {}).values()) == d.get("generated") and set(d.get("generated_by_file", {})) == {"calc.py", "headers.py"}, d.get("generated_by_file"))
code, text, d = mutate_json("--files", "headers.py", "--range", "all", "--test", "python strongtest.py")
check("with no mutant at all the per-file count is still written ({file: 0})", code == 0 and d.get("generated") == 0 and d.get("generated_by_file") == {"headers.py": 0}, d)
(mut / "headers.py").unlink()

print("impact.py")
imp = make_repo()
(imp / "branch.py").write_text("def a(x):\n    return x + 1\n\n\ndef b(x):\n    return x * 2\n", encoding="utf-8")
(imp / "atest.py").write_text("import sys\nsys.path.insert(0, '.')\nfrom branch import a\nprint(('  ok  ' if a(1) == 2 else '  FAIL  ') + 'a')\n", encoding="utf-8")
# a test that calls "b" through a child process — the same shape as a test that starts a script separately
(imp / "binner.py").write_text("import sys\nsys.path.insert(0, '.')\nfrom branch import b\nprint(b(2))\n", encoding="utf-8")
(imp / "btest.py").write_text("import subprocess, sys\nr = subprocess.run([sys.executable, 'binner.py'], capture_output=True, text=True)\n"
                              "print(('  ok  ' if r.stdout.strip() == '4' else '  FAIL  ') + 'b')\n", encoding="utf-8")
(imp / ".oqa").mkdir()
(imp / ".oqa" / "tests.json").write_text(json.dumps([{"name": "a", "command": "python atest.py"}, {"name": "b", "command": "python btest.py"}],
                                                    ensure_ascii=False), encoding="utf-8")
git(imp, "add", "-A")
git(imp, "commit", "-qm", "second")
code, text = run_tool("impact.py", "select", "--repo", imp)
check("selecting without a map stops with \"record first\" (does not say 0 tests selected)", code == 2 and "first" in text, text)
code, text = run_tool("impact.py", "record", "--repo", imp)
map_ = json.loads((imp / ".oqa" / "impact-map.json").read_text(encoding="utf-8"))
check("records that test a passed through the \"a\" line (2) of branch.py", 2 in map_["tests"]["a"].get("branch.py", []), map_["tests"].get("a"))
# child process recording · only the tests that pass through the changed lines are selected and run
check("the \"b\" line (6) called through a child process is recorded for test b too (child process tracing)", 6 in map_["tests"]["b"].get("branch.py", []), map_["tests"].get("b"))
check("test a did not pass through the \"b\" line", 6 not in map_["tests"]["a"].get("branch.py", []), map_["tests"].get("a"))
(imp / "branch.py").write_text("def a(x):\n    return x + 1\n\n\ndef b(x):\n    return x * 2 + 0\n", encoding="utf-8")
select_path = imp.parent / "select.json"
code, text = run_tool("impact.py", "select", "--repo", imp, "--run", "--json", select_path)
d = json.loads(select_path.read_text(encoding="utf-8"))
check("editing the \"b\" line selects only test b", set(d["selected"]) == {"b"}, d)
check("the selected test is run and its pass is recorded", d["results"] == {"b": "pass"}, d)
# a test that failed while the map was recorded stopped early — the map has few of its lines, so it was never selected and never run again
map_path = imp / ".oqa" / "impact-map.json"
map_text = map_path.read_text(encoding="utf-8")
map_path.write_text(json.dumps(dict(map_, results=dict(map_.get("results") or {}, a="fail")), ensure_ascii=False), encoding="utf-8")
code, text = run_tool("impact.py", "select", "--repo", imp, "--run", "--json", select_path)
d = json.loads(select_path.read_text(encoding="utf-8"))
check("a test that did not pass when the map was recorded is selected and run whatever line changed (it is not left out as \"does not reach the change\")",
      set(d["selected"]) == {"a", "b"} and d["results"] == {"a": "pass", "b": "pass"} and "did not pass when the map was recorded" in text, (d, text))
map_path.write_text(map_text, encoding="utf-8")
# the failures of the selected tests equal the failures of running everything
(imp / "branch.py").write_text("def a(x):\n    return x + 1\n\n\ndef b(x):\n    return x * 3\n", encoding="utf-8")
code, text = run_tool("impact.py", "select", "--repo", imp, "--run", "--json", select_path)
d = json.loads(select_path.read_text(encoding="utf-8"))
all_failed = {name for name, file in (("a", "atest.py"), ("b", "btest.py"))
              if "  FAIL  " in subprocess.run([sys.executable, file], cwd=str(imp), capture_output=True, text=True, encoding="utf-8", errors="replace",
                                              env={**os.environ, "PYTHONIOENCODING": "utf-8"}).stdout}
check("the failures of the selected tests {b} equal the failures of running everything · exit code 1", {name for name, value in d["results"].items() if value == "fail"} == all_failed == {"b"} and code == 1, (d, all_failed))
(imp / "branch.py").write_text("def a(x):\n    return x + 1\n\n\ndef b(x):\n    return x * 2 + 0\n", encoding="utf-8")
(imp / "branch.py").write_text("def a(x):\n    return x + 1\n\n\ndef b(x):\n    return x * 2\n\n\ndef c(x):\n    return x - 1\n", encoding="utf-8")
code, text = run_tool("impact.py", "select", "--repo", imp, "--json", select_path)
d = json.loads(select_path.read_text(encoding="utf-8"))
check("adding a place no test passes through is a \"gap\" · exit code 1", "branch.py" in d["gaps"] and code == 1, text)
(imp / "newfile.py").write_text("x = 1\n", encoding="utf-8")
(imp / "notes.md").write_text("text\n", encoding="utf-8")
code, text = run_tool("impact.py", "select", "--repo", imp, "--json", select_path)
d = json.loads(select_path.read_text(encoding="utf-8"))
check("a new file no test passed through is a gap too", "newfile.py" in d["gaps"], d)
check("a non-Python file is \"unseen\" (neither a gap nor a pass)", "notes.md" in d["unseen"] and "notes.md" not in d["gaps"], d)
git(imp, "add", "-A")
git(imp, "commit", "-qm", "third")
code, text = run_tool("impact.py", "select", "--repo", imp)
check("when HEAD changes it says the map is stale", "stale" in text, text)
(imp / "branch.py").write_text((imp / "branch.py").read_text(encoding="utf-8") + "\n# extra\n", encoding="utf-8")
run_tool("impact.py", "record", "--repo", imp)
code, text = run_tool("impact.py", "select", "--repo", imp)
check("a map recorded with modified .py files is reported as stale (the line numbers disagree with HEAD)", "recorded with modified .py files" in text, text)
git(imp, "checkout", "--", "branch.py")
run_tool("impact.py", "record", "--repo", imp)
# a deleted .py and a new .py that was only `git add`ed are matched as whole files · a deleted line starting with "-- " is not read as a header
(imp / "binner.py").unlink()
(imp / "added.py").write_text("y = 2\n", encoding="utf-8")
git(imp, "add", "added.py")
code, text = run_tool("impact.py", "select", "--repo", imp, "--json", select_path)
d = json.loads(select_path.read_text(encoding="utf-8"))
check("selects test b, which passed through the deleted file (binner.py)", "b" in d["selected"] and "binner.py" in d["changed"], d)
check("a new file that was only added (added.py) is matched as a whole file — no test passed through it, so a gap", "added.py" in d["gaps"], d)
git(imp, "rm", "-q", "--cached", "added.py")
(imp / "added.py").unlink()
git(imp, "checkout", "--", "binner.py")
code, text = run_tool("impact.py", "select", "--repo", imp, "--range", "HEAD~1")
check("when the range is not \"working\" the lines disagree with the HEAD-based map, so it is reported as stale", "stale" in text and "range" in text, text)

# beyond --max the front is not cut off; mutants are picked evenly — so they do not crowd the front lines
(mut / "many.py").write_text("".join("def f%d(x):\n    return x > %d\n\n\n" % (n, n) for n in range(10)), encoding="utf-8")
(mut / "manytest.py").write_text("print('  ok  end')\n", encoding="utf-8")
code, text = run_tool("mutate.py", "--repo", mut, "--files", "many.py", "--range", "all", "--test", "python manytest.py", "--max", "4", "--json", result_path)
d = json.loads(result_path.read_text(encoding="utf-8"))
lines = sorted({m["line"] for m in d["mutants"]})
check("with --max 4 the even pick reaches the functions at the back of the file (beyond line 30) · the generated count is kept", len(d["mutants"]) == 4 and lines[-1] > 30 and d["generated"] > 4, d)

# The places a person would break by hand in a guard script: the tool must build a mutant on the same line for each of them
# (a place inside the characters of a regular expression cannot be reached with ast). The guard below is a small stand-in with the same constructs
sys.path.insert(0, str(TOOLS))
import mutate                                            # noqa: E402
GUARD_SAMPLE = r'''import re

LOCAL = re.compile(r"^(?:127\.0\.0\.1|localhost)$")
PYTHON = re.compile(r"^(?:python|py)(?:\d|\.exe)?$")
ENV_PATTERN = re.compile(r"HOST=(\S+)")
PREFIXES = ("sudo", "env")
MODULE_SERVERS = {"http.server": "any"}
DEFAULT_ALL = ("flask", "uvicorn")
CODE_SUFFIXES = (".py", ".js")


def server_kind(word):
    return "python" if PYTHON.match(word) else None


def commands(command, shell):
    return command.split()


def _start_process(command):
    return []


def launch_reason(whole):
    return None


def port_zero(a):
    if a is None:
        return None
    else:
        return "0" if a == "0" else None


def guard(command, shell, kind, values, name, whole, text, start):
    k = server_kind(command)
    words = commands(command, shell) + _start_process(command)
    for head in words:
        if head in PREFIXES:
            continue
    module = words[0] if words else ""
    bound = MODULE_SERVERS[module] if module in MODULE_SERVERS else None
    for m in ENV_PATTERN.finditer(command or ""):
        bound = m.group(1)
    if kind in DEFAULT_ALL and not values:
        return "all"
    low = command.lower()
    if "--help" in low or "-?" in low:
        return None
    reason = None
    if name.endswith("run"):
        reason = "run"
    elif name.endswith("preview_start"):
        for part in (whole or ()):
            reason = launch_reason(whole) if whole is not None else None
            if "bind:allow" in text[start:start + 200]:
                return None
    if not (name.endswith(CODE_SUFFIXES) or name.startswith(".env")):
        return None
    return reason or bound or k
'''
hand_spots = ['LOCAL = re.compile(r"^(?:127', "    k = server_kind(command)\n", "    words = commands(command, shell) + _start_process(command)", "        if head in PREFIXES:",
              "if module in MODULE_SERVERS else None", 'PYTHON = re.compile(r"^(?:python', '    for m in ENV_PATTERN.finditer(command or ""):', "    if kind in DEFAULT_ALL and not values:",
              '    if "--help" in low or "-?" in low', '        return "0" if a == "0" else None', '    elif name.endswith("preview_start"):',
              "            reason = launch_reason(whole) if whole is not None else None", '            if "bind:allow" in text[start:', "    if not (name.endswith(CODE_SUFFIXES) or name.startswith(\".env\")):"]
tool_lines = {m["line"] for m in mutate.mutants(GUARD_SAMPLE, None)}
reached = [s for s in hand_spots if s in GUARD_SAMPLE and GUARD_SAMPLE.count("\n", 0, GUARD_SAMPLE.find(s)) + 1 in tool_lines]
check("all 14 hand-mutation places of the guard have a tool mutant", len(reached) == len(hand_spots) == 14, [s for s in hand_spots if s not in reached])

print("traceability.py")
tr = make_repo()
(tr / ".oqa" / "specs").mkdir(parents=True)
spec = tr / ".oqa" / "specs" / "2099-01-01_demo.md"
spec.write_text("# Spec\n\n## Expected results\n- E1 one that passes\n- E2 one that fails\n- E3 one without a marker\n- E4 a test that does not run\n\n## Results\n", encoding="utf-8")
(tr / "passtest.py").write_text("# verifies: 2099-01-01_demo/E1\nprint('  ok  a')\n", encoding="utf-8")
(tr / "failtest.py").write_text("# verifies: 2099-01-01_demo/E2\nprint('  FAIL  b')\n", encoding="utf-8")
(tr / "unruntest.py").write_text("# verifies: 2099-01-01_demo/E4\nprint('  ok  c')\n", encoding="utf-8")
(tr / ".oqa" / "tests.json").write_text(json.dumps([{"name": "passing", "command": "python passtest.py"}, {"name": "failing", "command": "python failtest.py"}],
                                                   ensure_ascii=False), encoding="utf-8")
trace_path = tr.parent / "trace.json"
code, text = run_tool("traceability.py", "--repo", tr, "--spec", spec, "--run", "--json", trace_path)
d = {item["id"]: item["verdict"] for item in json.loads(trace_path.read_text(encoding="utf-8"))["items"]}
check("tells apart marker and pass · marker and fail · no marker · outside test list", d == {"E1": "pass", "E2": "fail", "E3": "no marker", "E4": "outside test list"}, d)
check("exit code 1 when something is incomplete", code == 1, text)
code, text = run_tool("traceability.py", "--repo", tr, "--spec", spec, "--json", trace_path)
d = {item["id"]: item["verdict"] for item in json.loads(trace_path.read_text(encoding="utf-8"))["items"]}
check("not run and no map result either is \"not run\" (not flattened to pass)", d["E1"] == "not run", d)
# only an explicit pass is a pass: a result value the tool does not know (handed over by another tool) is "unknown", never "pass"
sys.path.insert(0, str(TOOLS))
import traceability                                      # noqa: E402
odd = {item["id"]: item["verdict"] for item in traceability.trace(tr, spec, run_tests=True, known_results={"passing": "timeout", "failing": "fail"})["items"]}
check("a known result that is neither pass nor fail (\"timeout\") makes the item \"unknown\" — not \"pass\" · a fail stays fail", odd.get("E1") == "unknown" and odd.get("E2") == "fail", odd)
odd = {item["id"]: item["verdict"] for item in traceability.trace(tr, spec, run_tests=True, known_results={"passing": "pass", "failing": "error"})["items"]}
check("an unrecognised value is not read as a failure either (\"error\" → unknown) · an explicit pass is a pass", odd.get("E1") == "pass" and odd.get("E2") == "unknown", odd)


def trace_code(*verdicts):
    return traceability.exit_code({"status": "present", "items": [{"verdict": v} for v in verdicts]})


check("exit code — an \"unknown\" item is cannot judge (2), never 0 · with a failed item it is 1", (trace_code("pass", "unknown"), trace_code("unknown", "fail"), trace_code("pass", "pass")) == (2, 1, 0),
      (trace_code("pass", "unknown"), trace_code("unknown", "fail"), trace_code("pass", "pass")))
old_spec = tr / ".oqa" / "specs" / "2099-01-02_old.md"
old_spec.write_text("# Spec\n\n## Expected results\n- something works\n- tests 0\n", encoding="utf-8")
code, text = run_tool("traceability.py", "--repo", tr, "--spec", old_spec, "--json", trace_path)
check("a spec whose expected results carry no numbers has the status \"no number\" · exit code 2 (it is not reported as a spec with 0 items)", json.loads(trace_path.read_text(encoding="utf-8"))["status"] == "no number" and code == 2, text)
mixed = tr / ".oqa" / "specs" / "2099-01-03_mixed.md"
mixed.write_text("# Spec\n\n## Expected results\n- E1 one that passes\n- a promise written without a number\n", encoding="utf-8")
(tr / "passtest.py").write_text("# verifies: 2099-01-01_demo/E1\n# verifies: 2099-01-03_mixed/E1\nprint('  ok  a')\n", encoding="utf-8")
code, text = run_tool("traceability.py", "--repo", tr, "--spec", mixed, "--run", "--json", trace_path)
d = [item["verdict"] for item in json.loads(trace_path.read_text(encoding="utf-8"))["items"]]
check("an unnumbered promise mixed into a numbered spec comes out as a \"no number\" item too · exit code 2", d == ["pass", "no number"] and code == 2, (d, text))
twice = tr / ".oqa" / "specs" / "2099-01-03_mixed.md"
twice.write_text("# Spec\n\n## Expected results\n- E1 one that passes\n- E1 a second promise that kept the number of the first\n- E2 nobody tagged this one\n", encoding="utf-8")
code, text = run_tool("traceability.py", "--repo", tr, "--spec", twice, "--run", "--json", trace_path)
d = [item["verdict"] for item in json.loads(trace_path.read_text(encoding="utf-8"))["items"]]
check("two items with the same number are both \"duplicate number\" — one tag does not pass them both (the item with its own number is judged as before)",
      d == ["duplicate number", "duplicate number", "no marker"], (d, text))
twice.write_text("# Spec\n\n## Expected results\n- E1 one that passes\n- E1 a second promise that kept the number of the first\n", encoding="utf-8")
code, text = run_tool("traceability.py", "--repo", tr, "--spec", twice, "--run", "--json", trace_path)
check("… and with nothing else wrong the exit code is 2 (cannot judge), not 0", code == 2 and "duplicate number" in text, (code, text))
# Test commands of other repositories do not always name the file path as is (module form · a whole folder). The marker in a test started as `python -m unittest tests.test_calc` must not come out as "outside test list"
module_spec = tr / ".oqa" / "specs" / "2099-01-04_module.md"
module_spec.write_text("# Spec\n\n## Expected results\n- E1 module form command\n- E2 a file the map knows\n- E3 a module with only a similar name\n\n## Results\n", encoding="utf-8")
(tr / "pkg").mkdir()
(tr / "pkg" / "__init__.py").write_text("", encoding="utf-8")
(tr / "pkg" / "modtest.py").write_text("# verifies: 2099-01-04_module/E1\nprint('  ok  a')\n", encoding="utf-8")
(tr / "pkg" / "scattered.py").write_text("# verifies: 2099-01-04_module/E2\nprint('  ok  b')\n", encoding="utf-8")
(tr / "pkg" / "modtest_other.py").write_text("# verifies: 2099-01-04_module/E3\nprint('  ok  c')\n", encoding="utf-8")
(tr / ".oqa" / "tests.json").write_text(json.dumps([{"name": "module", "command": "python -m pkg.modtest"}, {"name": "whole", "command": "python everything.py"},
                                                    {"name": "prefixed", "command": "python -m otherpkg.modtest"}, {"name": "suffixed", "command": "python -m pkg.modtest_longname"}], ensure_ascii=False), encoding="utf-8")   # another package with characters in front — must not be read as "module"
(tr / "everything.py").write_text("import pkg.scattered\n", encoding="utf-8")
head = subprocess.run(["git", "-C", str(tr), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()


def write_map_and_trace(head_value, whole_command):
    (tr / ".oqa" / "impact-map.json").write_text(json.dumps({"HEAD": head_value, "modified": [], "tests": {"module": {"pkg/modtest.py": [2]}, "whole": {"everything.py": [1], "pkg/scattered.py": [2]}},
                                                             "commands": {"module": "python -m pkg.modtest", "whole": whole_command}, "results": {}}, ensure_ascii=False), encoding="utf-8")
    code_, text_ = run_tool("traceability.py", "--repo", tr, "--spec", module_spec, "--run", "--json", trace_path)
    return code_, text_, {item["id"]: (item["verdict"], item["tests"]) for item in json.loads(trace_path.read_text(encoding="utf-8"))["items"]}


# with a stale map, or a test whose command changed afterwards, a file is not read as that test's
_, _, stale = write_map_and_trace("0" * 40, "python everything.py")
_, _, changed = write_map_and_trace(head, "python oldeverything.py")
check("the impact map is not used to find a marker's test when its HEAD differs from the HEAD now (stale) or when that test's command changed after the map was recorded — E2 is \"outside test list\" in both cases · E1 is still found from its module form command",
      bool(head) and stale["E2"][0] == "outside test list" and changed["E2"][0] == "outside test list" and stale["E1"] == ("pass", ["module"]), (head[:7], stale, changed))
code, text, d = write_map_and_trace(head, "python everything.py")
check("recognises the test file of a module form command (python -m pkg.modtest)", d["E1"] == ("pass", ["module"]), d)
check("without a path in the command it is still that test when the impact map knows the file as one that test ran", d["E2"] == ("pass", ["whole"]), d)
check("a module with only a similar name (pkg.modtest_other) is not the test of that command — outside test list", d["E3"][0] == "outside test list", d)

print("report.py · evidence fingerprint")
(imp / ".oqa" / "specs").mkdir(parents=True)
(imp / ".oqa" / "current").write_text("2099-01-01_proof.md\n", encoding="utf-8")
proof = imp / ".oqa" / "specs" / "2099-01-01_proof.md"
proof.write_text("# Spec\n\n## Expected results\n- E1 b doubles\n\n## Results\n- result text written by a person\n", encoding="utf-8")
(imp / "btest.py").write_text((imp / "btest.py").read_text(encoding="utf-8") + "# verifies: 2099-01-01_proof/E1\n", encoding="utf-8")
git(imp, "add", "-A")
git(imp, "commit", "-qm", "fourth")
run_tool("impact.py", "record", "--repo", imp)
(imp / "branch.py").write_text("def a(x):\n    return x + 1\n\n\ndef b(x):\n    return x * 2 if x >= 0 else x * 2\n", encoding="utf-8")
code, text = run_tool("report.py", "--repo", imp, "--spec", proof, "--max", "6")
proof_text = proof.read_text(encoding="utf-8")
check("writes the report under the spec's \"## Results\" and leaves the result text written by a person as it is", "### Verification Report" in proof_text and "result text written by a person" in proof_text, proof_text[-900:])
check("the report has the line expected result E1 ← b · pass", "| E1 b doubles | b | pass |" in proof_text, proof_text[-900:])
check("the report has the mutation count line (generated · run · killed)", "Mutation (changed lines): generated" in proof_text, proof_text[-900:])
code, text = run_tool("report.py", "--repo", imp, "--spec", proof, "--max", "6")
check("writing again replaces the report with one (does not pile up)", proof.read_text(encoding="utf-8").count("### Verification Report") == 1)
sys.path.insert(0, str(TOOLS))
import report                                            # noqa: E402
proof.write_text(proof.read_text(encoding="utf-8") + "\n## Appendix\n- later section\n", encoding="utf-8")
new_text = report.write_into(proof, "### Verification Report\n\nreplaced report\n")
check("even with another section after \"## Results\" the report stays inside the results section — before the later section", new_text.index("replaced report") < new_text.index("## Appendix") and new_text.count("### Verification Report") == 1, new_text[-400:])
# what the user writes below the report inside "## Results" (an equivalent-mutant line, review notes) is kept by the next run
kept = proof.parent / "kept.md"
kept.write_text("# Spec\n\n## Results\n- before the report\n\n" + report.report_text(fake_for_end := {"impact": {}, "mutation": {"none": "x"}, "spec": {"status": "present", "items": []},
                                                                                                    "time": "t", "HEAD": "h", "range": "working", "fingerprint": "fp"})
                + "\n- equivalent mutant: calc.py:10 compare Lt → LtE — the same here\nnotes of the review\n\n## Appendix\n- later\n", encoding="utf-8")
new_text = report.write_into(kept, "### Verification Report\n\nsecond report\n" + report.REPORT_END + "\n")
check("the report ends with its end line, and writing again replaces the old report up to that line only — the lines written below it stay (they used to be deleted, so a "
      "declared equivalent mutant came back as a survivor on the next run)",
      report.report_text(fake_for_end).rstrip("\n").endswith(report.REPORT_END) and new_text.count("### Verification Report") == 1 and "second report" in new_text
      and "- before the report\n" in new_text and "- equivalent mutant: calc.py:10 compare Lt → LtE — the same here\nnotes of the review\n" in new_text
      and new_text.count(report.REPORT_END) == 1 and new_text.index("notes of the review") < new_text.index("## Appendix"), new_text)
fake = {"impact": {"changed": ["a.py"], "selected": {"x": ["a.py"]}, "total": 1, "gaps": {}, "unseen": ["screen.js", ".oqa/current"], "results": {"x": "pass"}, "map_stale": True},
        "outside_code": ["screen.js"], "mutation": {"baseline": "pass", "generated": 3, "counts": {"killed": 0, "survived": 0, "crashed": 3},
                                                  "mutants": [{"file": "a.py", "line": n, "kind": "x", "verdict": "crashed", "basis": ""} for n in (1, 2, 3)]},      # all 3 generated mutants were run (a result with fewer rows than generated is a sampled run — unknown for that reason too)
        "spec": {"status": "present", "items": [{"id": "E1", "text": "a", "tests": ["x"], "verdict": "pass"}]}}
code_, incomplete_, unknown_ = report.judge(fake)
check("crashed · stale map · non-Python code are not \"all passed\" but unknown (exit code 2)", code_ == 2 and len(unknown_) == 3, unknown_)
code_, incomplete_, unknown_ = report.judge({**fake, "mutation": {**fake["mutation"], "mutants": fake["mutation"]["mutants"][:1]}})
check("… and when only 1 of the 3 generated mutants was run that is a fourth unknown (a sampled run)", code_ == 2 and len(unknown_) == 4 and any("only 1 of 3 mutants were run" in u for u in unknown_), unknown_)
fake2 = {**fake, "impact": {**fake["impact"], "map_stale": False}, "outside_code": [], "mutation": {"baseline": "fail", "generated": 4, "basis": "baseline output FAIL 1", "mutants": []}}
code_, incomplete_, unknown_ = report.judge(fake2)
check("a failed mutation baseline (original) is unknown, with its reason — not flattened to a pass",
      code_ == 2 and any("baseline (original) failed — baseline output FAIL 1" in u for u in unknown_), unknown_)

sys.path.insert(0, str(TOOLS))
import evidence                                          # noqa: E402
recorded = evidence.recorded_fingerprint(proof.read_text(encoding="utf-8"))
check("writing the report into the spec (.oqa/) leaves the fingerprint as it is — it is not stale the moment it is written", recorded and recorded == evidence.fingerprint(imp), (recorded, evidence.fingerprint(imp)))
# Documents are part of the product too. Editing a document outside .oqa/ changes the fingerprint, so the evidence becomes old evidence
(imp / "usage_fingerprint_check.md").write_text("# Usage\n", encoding="utf-8")
after_doc = evidence.fingerprint(imp)
(imp / "usage_fingerprint_check.md").unlink()
check("adding a document (.md) outside .oqa/ changes the fingerprint, and removing it brings it back", after_doc and after_doc != recorded and evidence.fingerprint(imp) == recorded, (recorded, after_doc))


# However the fingerprint is measured, its value must equal the plain way. The plain way is kept here as it is and compared
def old_fingerprint(root):
    with tempfile.TemporaryDirectory() as tmp_:
        env_ = dict(os.environ, GIT_INDEX_FILE=os.path.join(tmp_, "index"))
        for args_ in (["add", "-A", "--", "."], ["rm", "-r", "--cached", "-q", "--ignore-unmatch", "--", ".oqa"], ["write-tree"]):
            done_ = subprocess.run(["git", "-C", str(root), "-c", "core.quotepath=false", "-c", "core.autocrlf=false"] + args_, capture_output=True, text=True, env=env_)
        return done_.stdout.strip()[:12]


same = [evidence.fingerprint(imp) == old_fingerprint(imp)]
(imp / ".oqa" / "extra.md").write_text("inside .oqa\n", encoding="utf-8")
(imp / ".oqa" / "extra.json").write_text("{}", encoding="utf-8")
(imp / ".oqa_not").mkdir()
(imp / ".oqa_not" / "a.py").write_text("x = 1\n", encoding="utf-8")          # the name starts with ".oqa" but it is not the .oqa folder — it must not be left out
(imp / "sub" / ".oqa").mkdir(parents=True)
(imp / "sub" / ".oqa" / "b.py").write_text("y = 1\n", encoding="utf-8")       # only the .oqa at the root is left out — the same name in a subfolder is in the fingerprint
same.append(evidence.fingerprint(imp) == old_fingerprint(imp))
new_value = evidence.fingerprint(imp)
(imp / "sub" / ".oqa" / "b.py").write_text("y = 2\n", encoding="utf-8")
same.append(evidence.fingerprint(imp) == old_fingerprint(imp) != new_value)
import shutil as _shutil  # noqa: E402
for folder_ in (".oqa_not", "sub"):
    _shutil.rmtree(imp / folder_)
(imp / ".oqa" / "extra.md").unlink()
(imp / ".oqa" / "extra.json").unlink()
same.append(evidence.fingerprint(imp) == old_fingerprint(imp) == recorded)
check("the fingerprint value equals the plain way (add → rm --cached → write-tree) — at first · after editing inside .oqa · after adding a folder with a similar name and a \".oqa\" in a subfolder · after editing that file · after removing everything (back to the first value)",
      all(same) and len(same) == 4, same)
# A repository whose .gitignore ignores .oqa/ — leaving the excluded place out with a path condition (two git calls) made `git add` exit 1 there and the fingerprint could not be measured. That is why three calls stay
_ignore_before = (imp / ".gitignore").read_bytes() if (imp / ".gitignore").is_file() else None
(imp / ".gitignore").write_bytes((_ignore_before or b"") + b"\n.oqa/\n")
_ignored_value = evidence.fingerprint(imp)
check("the fingerprint is measured in a repository whose .gitignore ignores .oqa/ too (not None) · same value as the plain way", _ignored_value is not None and _ignored_value == old_fingerprint(imp), (_ignored_value, old_fingerprint(imp)))
if _ignore_before is None:
    (imp / ".gitignore").unlink()
else:
    (imp / ".gitignore").write_bytes(_ignore_before)
check("… after putting it back the fingerprint is the first value", evidence.fingerprint(imp) == recorded, (evidence.fingerprint(imp), recorded))

# a marker's test file is matched to a command by whole path words, not by a piece of text inside another path
print("traceability.py — which command runs a marker's file · manual_mutate.py — says when it records nothing")
COMMANDS = [("data", "python tests/data.py"), ("plain", "python a.py"), ("dotted", "python ./a.py -v"), ("quoted", "python \"a.py\""), ("backslash", "python .\\a.py"),
            ("node", "python -m pytest a.py::test_x -q"), ("subfolder", "python sub/a.py"), ("longer", "python extra.py"), ("other module", "python -m sub.a"),
            ("absolute", "python \"%s\"" % (tr / "a.py")), ("absolute elsewhere", "python %s" % (tr / "sub" / "a.py")), ("module", "python -m a")]
try:
    got = traceability.tests_for([{"name": name, "command": command} for name, command in COMMANDS], "a.py", None, tr)
except TypeError as error:                                # a tests_for that takes no repository cannot place an absolute path
    got = "raised %s" % error
check("tests_for matches the marker file `a.py` only to commands that name that very path as a whole word (plain · ./ · quoted · backslash · a pytest node id · the absolute path inside the repository · the module form)"
      " — `tests/data.py` contains the letters `a.py` and used to match, and so did `sub/a.py` and `extra.py`",
      got == ["plain", "dotted", "quoted", "backslash", "node", "absolute", "module"], got)
got = traceability.tests_for([{"name": name, "command": command} for name, command in COMMANDS], "tests/data.py")
check("… and `tests/data.py` is matched to its own command only", got == ["data"], got)
sub_spec = tr / ".oqa" / "specs" / "2099-01-05_sub.md"
sub_spec.write_text("# Spec\n\n## Expected results\n- E1 a marker in a file whose name is the tail of another test's path\n\n## Results\n", encoding="utf-8")
(tr / "st.py").write_text("# verifies: 2099-01-05_sub/E1\nprint('  ok  never run by a command')\n", encoding="utf-8")
(tr / ".oqa" / "tests.json").write_text(json.dumps([{"name": "passing", "command": "python passtest.py"}]), encoding="utf-8")
(tr / ".oqa" / "impact-map.json").unlink()
code, text = run_tool("traceability.py", "--repo", tr, "--spec", sub_spec, "--run", "--json", trace_path)
d = {item["id"]: (item["verdict"], item["tests"]) for item in json.loads(trace_path.read_text(encoding="utf-8"))["items"]}
check("end to end: the marker in `st.py` is not credited to the test `python passtest.py` (its path merely ends in those letters) — outside test list, exit code 1; it used to be \"pass\"",
      d == {"E1": ("outside test list", [])} and code == 1, (d, text))

import contextlib as _contextlib  # noqa: E402
import io as _io  # noqa: E402
import manual_mutate                                     # noqa: E402
manual_list = mut.parent / "boundary_list.json"
manual_list.write_text(json.dumps([{"name": "boundary", "file": "calc.py", "pairs": [[">= 60", "> 60"]], "test": "python strongtest.py"}]), encoding="utf-8")


def manual_run(fingerprints=None):
    """(exit code, printed text, the Manual Mutation lines in the evidence file) — fingerprints: the values the start and the end measurement give (None: the real ones)"""
    real_fingerprint, values, printed_ = manual_mutate.evidence.fingerprint, iter(fingerprints or ()), _io.StringIO()
    if fingerprints is not None:
        manual_mutate.evidence.fingerprint = lambda *a, **k: next(values)
    try:
        with _contextlib.redirect_stdout(printed_):
            code_ = manual_mutate.main(["--repo", str(mut), "--list", str(manual_list), "--jobs", "1", "--timeout", "60"])
    finally:
        manual_mutate.evidence.fingerprint = real_fingerprint
    return code_, printed_.getvalue(), [line for line in evidence.read(mut) if line.get("kind") == "Manual Mutation Test"]


code, text, lines_ = manual_run(["fingerprint_at_start", "fingerprint_at_end"])
check("manual mutation while the code changes under it: all killed (exit code 0), nothing is recorded — and it says so in one line (it used to say nothing, and the check stayed \"missing\" without a word)",
      code == 0 and lines_ == [] and "\nevidence not recorded — the code fingerprint changed while this ran (fingerprint_at_start → fingerprint_at_end)" in text, (code, text[-300:], lines_))
code, text, lines_ = manual_run([None, None])
check("… and when the fingerprint cannot be measured: nothing recorded, one line saying that", code == 0 and lines_ == [] and "\nevidence not recorded — the code fingerprint could not be measured" in text, (code, text[-300:], lines_))
code, text, lines_ = manual_run()
check("… with a steady fingerprint the evidence is recorded and no such line is printed",
      code == 0 and [(line["result"], line["name"]) for line in lines_] == [("pass", "boundary_list")] and "evidence not recorded" not in text, (code, text[-300:], lines_))

print("drafting.py")
dr = make_repo()
mutation_path = dr.parent / "draft_mutation.json"
run_tool("mutate.py", "--repo", dr, "--files", "calc.py", "--range", "all", "--test", "python weaktest.py", "--json", mutation_path)
bundles = dr.parent / "bundles"
code, text = run_tool("drafting.py", "bundle", "--repo", dr, "--result", mutation_path, "--out", bundles)
pages = sorted(bundles.glob("*.json"))
boundary = next((p for p in pages if "GtE → Gt" in json.loads(p.read_text(encoding="utf-8"))["kind"]), None)
check("writes a bundle (request page + spot) for every survived mutant · there is a bundle for the boundary mutant", code == 0 and len(pages) >= 1 and boundary is not None
      and (bundles / (boundary.stem + ".md")).is_file(), text)
strong = dr.parent / "strong_candidate.py"
strong.write_text("import sys\nsys.path.insert(0, '.')\nfrom calc import grade\nprint(('  ok  ' if grade(60) == 'accepted' else '  FAIL  ') + '60 boundary')\n", encoding="utf-8")
weak = dr.parent / "weak_candidate.py"
weak.write_text("import sys\nsys.path.insert(0, '.')\nfrom calc import grade\nprint(('  ok  ' if grade(90) == 'accepted' else '  FAIL  ') + '90')\n", encoding="utf-8")
wrong = dr.parent / "wrong_candidate.py"
wrong.write_text("print('  FAIL  from the start')\n", encoding="utf-8")
for candidate, wanted in ((strong, "accepted"), (weak, "rejected"), (wrong, "rejected")):
    code, text = run_tool("drafting.py", "check", "--repo", dr, "--bundle", boundary, "--candidate", candidate)
    check("draft check %s → %s (accepted only when it both passes on the original and fails on the mutant)" % (candidate.stem, wanted), text.strip().startswith(wanted) and code == (0 if wanted == "accepted" else 1), text)
check("checking happens only in a copy — the original calc.py is untouched", (dr / "calc.py").read_text(encoding="utf-8").count(">= 60") == 1)
# when the file changes after the mutant was generated the spot shifts: a stale bundle stops both check and bundle
(dr / "calc.py").write_text("# one more head line\n" + (dr / "calc.py").read_text(encoding="utf-8"), encoding="utf-8")
code, text = run_tool("drafting.py", "check", "--repo", dr, "--bundle", boundary, "--candidate", strong)
check("a bundle from before the file changed is \"rejected — stale bundle\" even for the strong draft", text.strip().startswith("rejected") and "stale" in text and code == 1, text)
code, text = run_tool("drafting.py", "bundle", "--repo", dr, "--result", mutation_path, "--out", dr.parent / "bundles2")
check("trying to write bundles after the file changed stops (exit code 2)", code == 2 and "stale" in text, text)
baseline_failed = dr.parent / "baseline_failed.json"
baseline_failed.write_text(json.dumps({"baseline": "fail", "generated": 3, "basis": "baseline output FAIL", "mutants": []}, ensure_ascii=False), encoding="utf-8")
code, text = run_tool("drafting.py", "bundle", "--repo", dr, "--result", baseline_failed, "--out", dr.parent / "bundles3")
check("a failed mutation baseline is not \"0 survived\" — it stops (exit code 2)", code == 2 and "baseline" in text and "0 survived mutants —" not in text, text)
# check needs both --bundle and --candidate
for missing in (("--bundle", boundary), ("--candidate", strong)):
    code, text = run_tool("drafting.py", "check", "--repo", dr, *missing)
    check("check with only %s stops (exit code 2)" % missing[0], code == 2 and "error:" in text, text)

print("\nfailures: %d" % len(failures))
sys.exit(1 if failures else 0)
