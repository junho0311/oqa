# -*- coding: utf-8 -*-
"""**Keeping the Verification Report light — each mutant runs only the tests that passed through its line, the original is run once, a gap is incomplete, and the verdicts equal the old way.**

    python tests/report_test.py        (from the repository root)
"""
import _harness  # noqa: F401,E402 — collects the temp folders in one folder for this test and removes it at the end (first)
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
scripts = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(scripts))
import traceability  # noqa: E402
import report  # noqa: E402

failures = []


def check(name, condition, text=""):
    print(("  ok  " if condition else "  FAIL  ") + name)
    if not condition:
        failures.append(name)
        if text:
            print("        " + str(text)[:500].replace("\n", " / "))


def git(root, *args):
    subprocess.run(["git", "-C", str(root)] + list(args), capture_output=True, check=True)


def run_tool(name, *args, cwd=None):
    done = subprocess.run([sys.executable, str(scripts / name)] + [str(a) for a in args], capture_output=True, cwd=cwd,
                          env=dict(os.environ, PYTHONIOENCODING="utf-8"))
    return done.returncode, done.stdout.decode("utf-8", "replace") + done.stderr.decode("utf-8", "replace")


CALC = ("def double(x):\n    return x * 2\n\n\ndef add(a, b):\n    return a + b\n\n\ndef unused(x):\n    return x > 3\n")
TEST_A = ("import calc\nprint('  ok  double' if calc.double(3) == 6 else '  FAIL  double')\n")
TEST_B = ("import calc\nprint('  ok  add' if calc.add(2, 3) == 5 else '  FAIL  add')\n")
TEST_C = ("import other\nprint('  ok  other' if other.one() == 1 else '  FAIL  other')\n")      # a test that does not pass through calc.py — tells file level from "all"
TESTS = [{"name": "A", "command": "python test_double.py"}, {"name": "B", "command": "python test_add.py"}, {"name": "C", "command": "python test_other.py"}]


def make_repo():
    root = Path(tempfile.mkdtemp()) / "small_repo"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.email", "t@t")
    git(root, "config", "user.name", "t")
    (root / "calc.py").write_text(CALC, encoding="utf-8")
    (root / "test_double.py").write_text(TEST_A, encoding="utf-8")
    (root / "test_add.py").write_text(TEST_B, encoding="utf-8")
    (root / "test_other.py").write_text(TEST_C, encoding="utf-8")
    (root / "other.py").write_text("def one():\n    return 1\n", encoding="utf-8")
    (root / ".oqa").mkdir()
    (root / ".oqa" / "tests.json").write_text(json.dumps(TESTS, ensure_ascii=False), encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "first")
    code, out = run_tool("impact.py", "record", "--repo", root)
    git(root, "add", "-A")
    return root, code, out


repo, code, out = make_repo()
map_ = json.loads((repo / ".oqa" / "impact-map.json").read_text(encoding="utf-8"))
check("impact map of the test repository — A passes through the body of double (line 2), B through the body of add (line 6)", 2 in map_["tests"]["A"].get("calc.py", []) and 6 in map_["tests"]["B"].get("calc.py", [])
      and 6 not in map_["tests"]["A"].get("calc.py", []), map_["tests"])
# the file now: two lines put at the top shift the line numbers, the body of add is edited, and there is one new file
(repo / "calc.py").write_text("# header\n\n" + CALC.replace("return a + b\n", "return (a + b)\n"), encoding="utf-8")
(repo / "fresh.py").write_text("def triple(x):\n    return x * 3\n", encoding="utf-8")
tests_path = repo.parent / "tests.json"
# test D added after the map was recorded — it is not in the map, so which lines it passes through is "unknown" → it is put into every mutant (otherwise a new test is never selected and the mutants only it guards survive)
(repo / "test_new.py").write_text(TEST_A.replace("double'", "double new'"), encoding="utf-8")
tests_path.write_text(json.dumps(TESTS + [{"name": "D", "command": "python test_new.py"}], ensure_ascii=False), encoding="utf-8")
(repo.parent / "tests_old.json").write_text(json.dumps(TESTS, ensure_ascii=False), encoding="utf-8")      # a list without D — to see the gap branch
DEFAULT = ["--test", "python test_double.py", "--test", "python test_add.py", "--test", "python test_other.py"]


def mutate_run(*extra, files=("calc.py",), range_="all"):
    json_path = repo.parent / ("mut_%d.json" % len(list(repo.parent.glob("mut_*.json"))))
    code, out = run_tool("mutate.py", "--repo", repo, "--files", *files, "--range", range_, *DEFAULT, "--json", json_path, "--timeout", "60", *extra)
    return code, out, (json.loads(json_path.read_text(encoding="utf-8")) if json_path.is_file() else {})


# ── the tests selected for each mutant ──
code, out, new = mutate_run("--map", repo / ".oqa" / "impact-map.json", "--tests-file", tests_path)
rows = new.get("mutants", [])
double_rows = [r for r in rows if r["line"] == 4]                # old line 2 — shifted by two lines, now line 4
add_rows = [r for r in rows if r["line"] == 8]                   # the edited line (no old line)
unused_rows = [r for r in rows if r["line"] == 12]               # no test passes through it
check("when added lines move a line down, its mutant is looked up in the map by the old line number, so only the tests that passed through that line run (body of double → A, plus D which is not in the map) — line level",
      double_rows and all(r["tests"] == ["D", "A"] and r["selection"] == "line level" and r["verdict"] == "killed" for r in double_rows), double_rows)
check("an edited line (no old line) uses B, which passed through the old lines of that hunk (old line 6 of the body of add), + D which is not in the map (hunk level)",
      add_rows and all(r["tests"] == ["D", "B"] and r["selection"] == "hunk level" for r in add_rows), add_rows)
# hunk level — pick_tests is called directly (including the branch that falls back to file level when no test passed through the old lines of the hunk)
sys.path.insert(0, str(scripts))
import mutate  # noqa: E402


def fake_old_line(hunk):
    """new lines 5 · 6 have no old line (old lines of the hunk = the argument), the rest stay as they are"""
    f = lambda new: None if new in (5, 6) else new      # noqa: E731
    f.hunk = lambda new: set(hunk) if new in (5, 6) else set()
    return f


fake_map = {"P": {"f.py": [2, 3]}, "Q": {"f.py": [9]}, "R": {"g.py": [1]}}
check("hunk level — only P, which passed through the old line (3) of the changed hunk", mutate.pick_tests(fake_map, "f.py", fake_old_line({3}), {"line": 5}, ["P", "Q", "R"]) == (["P"], "hunk level"))
check("hunk level — a hunk that only adds uses the old lines before and after (8 · 9) → Q", mutate.pick_tests(fake_map, "f.py", fake_old_line({8, 9}), {"line": 6}, ["P", "Q", "R"]) == (["Q"], "hunk level"))
check("when no test passed through the old lines of the hunk, every test that passed through that file (file level — unknown is not flattened into does-not-pass)",
      mutate.pick_tests(fake_map, "f.py", fake_old_line({40}), {"line": 5}, ["P", "Q", "R"]) == (["P", "Q"], "file level"))
check("a node spanning several lines — a line with an old line (3 → P) and the old lines of the hunk (9 → Q) are looked at together",
      mutate.pick_tests(fake_map, "f.py", fake_old_line({9}), {"line": 3, "end_line": 5}, ["P", "Q", "R"]) == (["P", "Q"], "hunk level"))
# matched with a real git diff — calc.py now has two lines added at the top (a hunk at old line 0) and the body of add (old line 6) changed
real_old = mutate.old_line_mapper(repo, "calc.py")
check("old_line_mapper.hunk — a hunk that only adds gives the old lines before and after (0 · 1), a changed hunk its old line range (6), outside a hunk the empty set",
      real_old.hunk(1) == {0, 1} and real_old.hunk(2) == {0, 1} and real_old.hunk(8) == {6} and real_old.hunk(4) == set() and real_old(4) == 2 and real_old(8) is None,
      (real_old.hunk(1), real_old.hunk(8), real_old.hunk(4), real_old(4)))


# fast line recording — when a Python 3.12 or later exists, the sys.monitoring map and the settrace (forced) map of the same Python are compared. Without one it says "skip" (not counted as a pass)
def newer_python():
    if sys.version_info >= (3, 12):                         # the interpreter running this test already qualifies (the `py` launcher exists only on Windows)
        return sys.executable
    for version in ("3.14", "3.13", "3.12"):
        try:
            done = subprocess.run(["py", "-" + version, "-c", "import sys; print(sys.executable)"], capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            return None
        if done.returncode == 0 and done.stdout.strip():
            return done.stdout.strip()
    return None


new_py = newer_python()
if new_py:
    watched = Path(tempfile.mkdtemp()) / "watched_repo"
    (watched / "pkg").mkdir(parents=True)
    git(watched, "init", "-q")
    (watched / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (watched / "pkg" / "job.py").write_text(
        "import functools, threading\n\n\ndef deco(f):\n    @functools.wraps(f)\n    def wrapped(*a):\n        return f(*a)\n    return wrapped\n\n\n"
        "@deco\ndef total(n):\n    s = 0\n    for i in range(n):\n        if i % 2:\n            s += i\n    return s\n\n\n"
        "class Counter:\n    def __init__(self):\n        self.value = 0\n\n    def add(self, x):\n        self.value += x\n        return self\n\n\n"
        "def work(results):\n    results.append(total(5))\n\n\ndef unused():\n    return 1\n", encoding="utf-8")
    (watched / "test_job.py").write_text(
        "import subprocess, sys, threading\nfrom pkg import job\nr = []\nt = threading.Thread(target=job.work, args=(r,))\nt.start(); t.join()\n"
        "print(job.total(4), job.Counter().add(2).value, r)\nsubprocess.run([sys.executable, '-c', 'from pkg import job; print(job.Counter().value)'])\n"
        # if the recorder took the coverage id (1), a coverage tool inside the test could not get its id — it must be free
        "print('  FAIL  the recorder holds the coverage id' if sys.monitoring.get_tool(sys.monitoring.COVERAGE_ID) else '  ok  the coverage id is free')\n", encoding="utf-8")
    (watched / ".oqa").mkdir()
    (watched / ".oqa" / "tests.json").write_text(json.dumps([{"name": "job", "command": '"%s" test_job.py' % new_py}], ensure_ascii=False), encoding="utf-8")
    git(watched, "add", "-A")
    git(watched, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "first")
    maps, took, results_by = {}, {}, {}
    for method, extra_env in (("monitoring", {}), ("settrace", {"OQA_TRACE_SETTRACE": "1"})):
        started = time.time()
        done = subprocess.run([sys.executable, str(scripts / "impact.py"), "record", "--repo", str(watched)], capture_output=True,
                              env=dict(os.environ, PYTHONIOENCODING="utf-8", **extra_env))
        took[method] = round(time.time() - started, 2)
        whole = json.loads((watched / ".oqa" / "impact-map.json").read_text(encoding="utf-8"))
        maps[method], results_by[method] = whole["tests"]["job"], whole["results"]["job"]
    check("while recording, the coverage id (1) inside the test stays free (the recorder uses ids 3 · 4)", results_by["monitoring"] == "pass", results_by)
    check("3.12 or later (%s): sys.monitoring map = settrace map (decorator · loop · class · thread · child process) · seconds %s" % (Path(new_py).parent.name, took),
          maps["monitoring"] == maps["settrace"] and maps["monitoring"].get("pkg/job.py"), maps)
    check("the body (34) of a function never called (unused) is not in the map · the line run in a thread (30) is · no \"<frozen …>\" · no line 0",
          34 not in maps["monitoring"].get("pkg/job.py", []) and 30 in maps["monitoring"].get("pkg/job.py", [])
          and not any(f.startswith("<") or "frozen" in f for f in maps["monitoring"]) and all(0 not in numbers for numbers in maps["monitoring"].values()), maps["monitoring"])
    shutil.rmtree(watched.parent, ignore_errors=True)
else:
    print("  skip  no Python 3.12 or later, so the sys.monitoring comparison could not be made (not counted as a pass)")
# a hunk that changes several lines — when old lines 2 · 3 · 4 are changed the old lines of that hunk are {2, 3, 4} (not only the first line)
multi = Path(tempfile.mkdtemp()) / "multi_line_hunk"
multi.mkdir()
git(multi, "init", "-q")
(multi / "a.py").write_text("x = 0\na = 1\nb = 2\nc = 3\ny = 9\n", encoding="utf-8")
git(multi, "add", "-A")
git(multi, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "first")
(multi / "a.py").write_text("x = 0\na = 10\nb = 20\nc = 30\ny = 9\n", encoding="utf-8")
multi_old = mutate.old_line_mapper(multi, "a.py")
check("old_line_mapper.hunk — a hunk that changed three lines gives the whole old line range {2,3,4}", multi_old.hunk(3) == {2, 3, 4} and multi_old(5) == 5, multi_old.hunk(3))
shutil.rmtree(multi.parent, ignore_errors=True)
check("split_lines — like ast, splits only at \\r\\n · \\r · \\n (not at \\x0c · \\u2028) · with keepends the line ends are kept",
      mutate.split_lines("a\x0cb\nc d\r\ne") == ["a\x0cb", "c d", "e"] and mutate.split_lines("a\r\nb\n", keepends=True) == ["a\r\n", "b\n"])
import re as _re  # noqa: E402
_pattern = _re.compile(mutate.DEFAULT_FAIL_PATTERN)
check("the default fail pattern recognises pytest failures (\"FAILED tests/…\" · \"=== 2 failed, 3 passed ===\" · \"=== 1 error ===\") — not a pass line · not 0 failed",
      all(_pattern.search(s) for s in ("FAILED tests/test_a.py::test_b - assert 1 == 2", "====== 2 failed, 3 passed in 0.12s ======", "==== 1 error in 0.3s ====", "  FAIL  old form"))
      and not any(_pattern.search(s) for s in ("====== 5 passed in 0.12s ======", "tests/test_a.py::test_b PASSED", "=== 0 failed ===", "result: FAILED it is not")))
check("at hunk level too, tests that are not in the map are always included", mutate.pick_tests(fake_map, "f.py", fake_old_line({3}), {"line": 5}, ["P"], unmapped=["Z"]) == (["P", "Z"], "hunk level"))
check("a mutant no test passed through still runs the test that is not in the map (D) — when that does not catch it either, survived (D only looks at double)",
      unused_rows and all(r["tests"] == ["D"] and r["verdict"] == "survived" for r in unused_rows), unused_rows)
code_g, out_g, gap_result = mutate_run("--map", repo / ".oqa" / "impact-map.json", "--tests-file", repo.parent / "tests_old.json")
gap_rows = [r for r in gap_result.get("mutants", []) if r["line"] == 12]
check("with no test outside the map and no test that passed through, it is not run and is a \"gap\" (not added to survived · killed)", gap_rows and all(r["verdict"] == "gap" and r["tests"] == [] for r in gap_rows)
      and gap_result.get("counts", {}).get("gap") == len(gap_rows) and code_g == 1, (gap_rows, gap_result.get("counts")))
code, out, fresh = mutate_run("--map", repo / ".oqa" / "impact-map.json", "--tests-file", tests_path, files=("fresh.py",))
check("a file not in the map (a new file after recording) uses every --test + the tests that are not in the map (\"file not in map\")",
      fresh.get("mutants") and all(r["selection"] == "file not in map" and r["tests"] == ["D", "A", "B", "C"] for r in fresh["mutants"]), fresh.get("mutants"))
# when every mutant of new files runs every test the cost explodes — for a file not in the map only the tests whose text mentions that module name (+ tests not in the map) are selected.
# A test that does not mention the name may still pass through that file — then only "survived" grows, no false "killed" appears (the strict side). When no test mentions the name, all tests as before
(repo / "test_fresh_seen.py").write_text("import fresh\nprint('  ok  triple' if fresh.triple(2) == 6 else '  FAIL  triple')\n", encoding="utf-8")
named_tests_path = repo.parent / "tests_named.json"
named_tests_path.write_text(json.dumps(TESTS + [{"name": "D", "command": "python test_new.py"}, {"name": "seen", "command": "python test_fresh_seen.py"}], ensure_ascii=False), encoding="utf-8")
_map_data = json.loads((repo / ".oqa" / "impact-map.json").read_text(encoding="utf-8"))
_map_data["tests"]["seen"] = {"test_fresh_seen.py": [1, 2]}      # "seen" is a test in the map (it must be selected by name, not because tests outside the map always go in)
named_map = repo.parent / "impact-map_named.json"
named_map.write_text(json.dumps(_map_data, ensure_ascii=False), encoding="utf-8")
code, out, named = mutate_run("--map", named_map, "--tests-file", named_tests_path, files=("fresh.py",))
check("a file not in the map — only the test whose text mentions the module name (seen) and the test not in the map (D) are selected (\"name level\") · that test kills the mutant · A · B · C do not run",
      named.get("mutants") and all(r["selection"] == "name level" and sorted(r["tests"]) == ["D", "seen"] for r in named["mutants"]) and any(r["verdict"] == "killed" for r in named["mutants"]),
      [(r["selection"], r["tests"], r["verdict"]) for r in named.get("mutants", [])] or out[-300:])
(repo / "test_fresh_seen.py").unlink()

# ── same verdicts as the old way (every test for every mutant) with fewer runs ──
code, out, old = mutate_run()
key = lambda r: (r["line"], r["spot"]["col"], r["kind"])        # noqa: E731
old_verdicts = {key(r): r["verdict"] for r in old.get("mutants", [])}
new_verdicts = {key(r): r["verdict"] for r in rows}
different = [(k, old_verdicts.get(k), new_verdicts.get(k)) for k in old_verdicts if not (old_verdicts[k] == new_verdicts.get(k) or (new_verdicts.get(k) == "gap" and old_verdicts[k] == "survived"))]
check("for each same mutant the verdict equals the old way (gap ↔ old \"survived\" — with no test passing through, neither way can catch it) · mutants %d" % len(old_verdicts),
      len(old_verdicts) >= 5 and set(old_verdicts) == set(new_verdicts) and not different and "survived" in new_verdicts.values() and "killed" in new_verdicts.values(), (len(old_verdicts), different, new_verdicts))
check("the new way runs fewer tests than the old way (old %s · new %s)" % (old.get("test_runs"), new.get("test_runs")), 0 < new.get("test_runs", 0) < old.get("test_runs", 0))

# ── stop on a kill · end at the failure line · in parallel ──
check("a killed mutant stops at the first test (body of double — the new test D, not in the map, runs first and kills it there) · a mutant that is not killed runs every selected test (new file — four)",
      double_rows and all(r["ran"] == 1 and r["verdict"] == "killed" for r in double_rows) and fresh["mutants"] and all(r["ran"] == 4 and r["verdict"] == "survived" for r in fresh["mutants"]),
      ([r["ran"] for r in double_rows], [r["ran"] for r in fresh.get("mutants", [])]))
import impact as _impact0  # noqa: E402
(repo / "test_fail_then_long.py").write_text("import time\nprint('  ok  before')\nprint('  FAIL  middle')\ntime.sleep(40)\nprint('  ok  after')\n", encoding="utf-8")
_t0 = time.time()
code_, out_, err_, hit_ = _impact0.run_streaming("python test_fail_then_long.py", repo, 60, None, mutate.DEFAULT_FAIL_PATTERN)
check("a test is stopped as soon as it prints a failure line (a test that would then sleep 40 seconds ends in %.1f seconds) · the matched line is returned · nothing printed after it is in the output" % (time.time() - _t0),
      time.time() - _t0 < 20 and hit_ == "FAIL  middle" and b"after" not in out_, (hit_, out_[-60:]))
code_, out_, err_, hit_ = _impact0.run_streaming("python test_double.py", repo, 60, None, mutate.DEFAULT_FAIL_PATTERN)
check("a test without a failure line runs to the end with exit code 0 · no matched line · the whole output", code_ == 0 and hit_ is None and "  ok  " in out_.decode("utf-8", "replace"), (code_, hit_, out_))
(repo / "test_fail_then_long.py").unlink()
(repo / "test_endless.py").write_text("import time\ntime.sleep(40)\n", encoding="utf-8")
_t0 = time.time()
code_, out_, err_, hit_ = _impact0.run_streaming("python test_endless.py", repo, 3, None, mutate.DEFAULT_FAIL_PATTERN)
check("reading line by line also ends on timeout (exit code None · no matched line — that is not killed)", code_ is None and hit_ is None and time.time() - _t0 < 25, (code_, hit_))
(repo / "test_endless.py").unlink()
# a test ended at its failure line cannot delete its own temp folder (it ends before cleaning up) — run_tests points the temp place of the test at its own folder and removes it
(repo / "test_leftover_tmp.py").write_text("import tempfile, time\nd = tempfile.mkdtemp(prefix='leftover_test_')\nopen('left_place.txt', 'w', encoding='utf-8').write(d)\n"
                                           "print('  FAIL  leftover', flush=True)\ntime.sleep(40)\n", encoding="utf-8")
failed_, _, _, tail_ = mutate.run_tests(["python test_leftover_tmp.py"], repo, mutate.DEFAULT_FAIL_PATTERN, 60, stop_on_kill=True)
left_place = Path((repo / "left_place.txt").read_text(encoding="utf-8")) if (repo / "left_place.txt").is_file() else None
check("the temp folder made by a test ended at its failure line does not stay (the temp place of the test is pointed at a folder made by run_tests and removed)",
      failed_ is True and left_place is not None and not left_place.exists() and not left_place.parent.exists(), (failed_, left_place))
for f_ in ("test_leftover_tmp.py", "left_place.txt"):
    (repo / f_).unlink()
# a grandchild process whose parent exited first — not found through the process tree (taskkill /T). Leftover processes started by a test kept the copy folder locked (the copy could not be removed)
(repo / "test_leftover_grandchild.py").write_text(
    "import subprocess, sys, time\n"
    "middle = \"import subprocess, sys; p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(90)']); open('grandchild.txt', 'w').write(str(p.pid))\"\n"
    "subprocess.run([sys.executable, '-c', middle])\nprint('  FAIL  grandchild', flush=True)\ntime.sleep(40)\n", encoding="utf-8")


def alive(pid):
    done_ = subprocess.run(["tasklist", "/FI", "PID eq %s" % pid, "/NH"], capture_output=True, text=True, errors="replace") if os.name == "nt" else None
    return (str(pid) in done_.stdout) if done_ else os.path.exists("/proc/%s" % pid)


code_, out_, err_, hit_ = _impact0.run_streaming("python test_leftover_grandchild.py", repo, 60, None, mutate.DEFAULT_FAIL_PATTERN)
grandchild = (repo / "grandchild.txt").read_text(encoding="utf-8").strip() if (repo / "grandchild.txt").is_file() else ""
time.sleep(1)
check("when ending at the failure line, a grandchild process whose parent exited first is ended too (it does not stay and hold the copy folder)", hit_ == "FAIL  grandchild" and grandchild.isdigit() and not alive(grandchild), (hit_, grandchild))
if grandchild.isdigit() and alive(grandchild):
    subprocess.run(["taskkill", "/F", "/PID", grandchild], capture_output=True)
(repo / "test_leftover_grandchild.py").write_text(
    "import subprocess, sys\n"
    "middle = \"import subprocess, sys; p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(90)']); open('grandchild.txt', 'w').write(str(p.pid))\"\n"
    "subprocess.run([sys.executable, '-c', middle])\nprint('  ok  grandchild')\n", encoding="utf-8")
code_, out_, err_ = _impact0.run_command("python test_leftover_grandchild.py", repo, 60, None)
grandchild = (repo / "grandchild.txt").read_text(encoding="utf-8").strip()
time.sleep(1)
check("when the test ends properly, the processes it left are ended as well (exit code 0 · output as it is)", code_ == 0 and "ok  grandchild" in out_.decode("utf-8", "replace") and not alive(grandchild), (code_, grandchild))
if alive(grandchild):
    subprocess.run(["taskkill", "/F", "/PID", grandchild], capture_output=True)
for f_ in ("test_leftover_grandchild.py", "grandchild.txt"):
    (repo / f_).unlink()
code2, out2, two = mutate_run("--map", repo / ".oqa" / "impact-map.json", "--tests-file", tests_path, "--jobs", "2")
two_verdicts = {key(r): r["verdict"] for r in two.get("mutants", [])}
check("the verdicts of --jobs 2 equal --jobs 1 for each mutant and the order is the same · no copy stays", two_verdicts == new_verdicts and [key(r) for r in two["mutants"]] == [key(r) for r in rows]
      and not [p for p in Path(tempfile.gettempdir()).glob("oqa_mutate_*")], (two_verdicts, [p.name for p in Path(tempfile.gettempdir()).glob("oqa_mutate_*")]))
exclusive_tests_path = repo.parent / "tests_exclusive.json"
exclusive_tests_path.write_text(json.dumps([dict(t, exclusive=True, seconds=1.0) for t in TESTS] + [{"name": "D", "command": "python test_new.py", "seconds": 0.1}], ensure_ascii=False), encoding="utf-8")
code3, out3, three = mutate_run("--map", repo / ".oqa" / "impact-map.json", "--tests-file", exclusive_tests_path, "--jobs", "3")
check("when the tests are marked \"exclusive\" (run one at a time) and the test list gives each test's run time in seconds, the verdicts are the same — the fast test (D, 0.1 seconds) runs first and the double mutants are killed at D (test order D · A)",
      {key(r): r["verdict"] for r in three.get("mutants", [])} == new_verdicts and all(r["tests"] == ["D", "A"] and r["ran"] == 1 for r in three["mutants"] if r["line"] == 4),
      [(r["line"], r["tests"], r["ran"]) for r in three.get("mutants", [])])

# grouping cases — the cases of the same test file that one mutant selected run as one program (`base --case a,b`) · the verdicts are the same
(repo / "test_grouped.py").write_text(
    "import sys\nsys.path.insert(0, r'%s')\nimport _harness\nimport calc\n\n\ndef finish():\n    return 0\n\n\n"
    "raise SystemExit(_harness.run_cases(__file__, globals(), finish))\n# case: single\nprint('  ok  single' if calc.double(3) == 6 else '  FAIL  single')\n"
    "# case: pair\nprint('  ok  pair' if calc.add(2, 3) == 5 else '  FAIL  pair')\n" % Path(__file__).resolve().parent, encoding="utf-8")
grouped_tests_path = repo.parent / "tests_grouped.json"
grouped_tests_path.write_text(json.dumps([{"name": "grp:%s" % n, "command": "python test_grouped.py --case %s" % n, "case_base": "python test_grouped.py", "case_name": n} for n in ("single", "pair")],
                                         ensure_ascii=False), encoding="utf-8")
json_ = repo.parent / "mut_grouped.json"
code_, out_ = run_tool("mutate.py", "--repo", repo, "--files", "calc.py", "--range", "all", "--test", "python test_grouped.py --case single", "--test", "python test_grouped.py --case pair",
                       "--tests-file", grouped_tests_path, "--json", json_, "--timeout", "60")
grouped = json.loads(json_.read_text(encoding="utf-8")) if json_.is_file() else {}
check("grouping cases — the two selected cases (same test file) run as one command (ran 1 for each mutant) · the double · add mutants are killed and the function nobody looks at survives",
      grouped.get("mutants") and all(r["tests"] == ["grp:pair", "grp:single"] and r["ran"] == 1 for r in grouped["mutants"])
      and {r["verdict"] for r in grouped["mutants"] if r["line"] in (4, 8)} == {"killed"} and {r["verdict"] for r in grouped["mutants"] if r["line"] == 12} == {"survived"},
      [(r["line"], r["tests"], r["ran"], r["verdict"]) for r in grouped.get("mutants", [])] or out_[-300:])
(repo / "test_grouped.py").unlink()


# ── the manual mutation tool: baseline pass first · killed by a failure line · the copies are removed ──
def manual(mutations, *extra):
    list_path = repo.parent / "manual_list.json"
    list_path.write_text(json.dumps(mutations, ensure_ascii=False), encoding="utf-8")
    return run_tool("manual_mutate.py", "--repo", repo, "--list", list_path, "--timeout", "60", *extra)


code_, out_ = manual([{"name": "double to triple", "file": "calc.py", "pairs": [["return x * 2", "return x * 3"]], "test": "A"},
                      {"name": "function nobody looks at", "file": "calc.py", "pairs": [["return x > 3", "return x > 4"]], "test": "A"}], "--jobs", "2")
check("manual mutation — a spot the repro test bites is killed, a spot it does not bite survives · exit code 1 · the baseline pass is printed first · no copy stays",
      code_ == 1 and _re.search(r"(?m)^killed\s+double to triple", out_) and _re.search(r"(?m)^survived\s+function nobody looks at", out_) and "baseline A" in out_ and "pass" in out_ and "killed 1 · survived 1" in out_
      and not list(Path(tempfile.gettempdir()).glob("oqa_manual_mutate_*")), out_)
# an "exclusive" test runs one at a time in manual mutation too (a failure line printed because tests using the same port · file overlapped would become a false "killed")
overlap_mark = repo.parent / "overlap_mark.txt"
(repo / "test_exclusive.py").write_text("import os, time\np = r'%s'\nif os.path.exists(p):\n    print('  FAIL  ran overlapped')\nelse:\n    open(p, 'w').close()\n    time.sleep(2)\n    os.remove(p)\n"
                                        "    print('  ok  exclusive')\n" % overlap_mark, encoding="utf-8")
(repo / "solo_target.py").write_text("a = 1\nb = 2\n", encoding="utf-8")
_old_list = (repo / ".oqa" / "tests.json").read_text(encoding="utf-8")
(repo / ".oqa" / "tests.json").write_text(json.dumps(json.loads(_old_list) + [{"name": "solo", "command": "python test_exclusive.py", "exclusive": True}], ensure_ascii=False), encoding="utf-8")
code_, out_ = manual([{"name": "exclusive one", "file": "solo_target.py", "pairs": [["a = 1", "a = 3"]], "test": "solo"},
                      {"name": "exclusive two", "file": "solo_target.py", "pairs": [["b = 2", "b = 4"]], "test": "solo"}], "--jobs", "2")
check("manual mutation — an \"exclusive\" test does not overlap even with jobs 2 (two spots that test does not look at → survived 2; a failure line from overlapping runs is not counted as killed)",
      "killed 0 · survived 2" in out_ and "overlapped" not in out_, out_)
(repo / ".oqa" / "tests.json").write_text(_old_list, encoding="utf-8")
for f_ in ("test_exclusive.py", "solo_target.py"):
    (repo / f_).unlink()
code_, out_ = manual([{"name": "double to triple", "file": "calc.py", "pairs": [["return x * 2", "return x * 3"]], "test": "A"}])
check("manual mutation — exit code 0 when all are killed", code_ == 0 and "total 1 · killed 1" in out_, out_)


# manual mutation is verification too — when the tool ends it leaves one "Manual Mutation Test" line in the evidence record (under the list file name)
def manual_evidence():
    path_ = repo / ".oqa" / "evidence.jsonl"
    lines_ = [json.loads(s_) for s_ in path_.read_text(encoding="utf-8").splitlines()] if path_.is_file() else []
    return [d for d in lines_ if d.get("kind") == "Manual Mutation Test"]


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import evidence as _evidence0  # noqa: E402
last_line = (manual_evidence() or [{}])[-1]
check("manual mutation — when all are killed the evidence record has Manual Mutation Test · pass · the fingerprint of the code now · the name is the list file name",
      last_line.get("result") == "pass" and last_line.get("fingerprint") == _evidence0.fingerprint(repo) and last_line.get("name") == "manual_list" and last_line.get("tool") == "manual_mutate.py"
      and "killed 1" in last_line.get("basis", ""), last_line)
manual([{"name": "function nobody looks at", "file": "calc.py", "pairs": [["return x > 3", "return x > 4"]], "test": "A"}])
check("manual mutation — when a spot survives the evidence is fail", (manual_evidence() or [{}])[-1].get("result") == "fail", manual_evidence()[-1:])
(repo.parent / "touch.py").write_text("open(r'%s', 'w').write('x')\nprint('  ok  touch')\n" % (repo / "running.txt"), encoding="utf-8")
_count_before = len(manual_evidence())
manual([{"name": "the repository changes during the run", "file": "calc.py", "pairs": [["return x > 3", "return x > 4"]], "test": 'python "%s"' % (repo.parent / "touch.py")}])
check("manual mutation — when the repository code changes during the run (start fingerprint ≠ end fingerprint) no evidence is recorded (which code the result belongs to is unknown)",
      len(manual_evidence()) == _count_before and (repo / "running.txt").is_file(), manual_evidence()[-1:])
(repo / "running.txt").unlink()
code_, out_ = manual([{"name": "with a wrong test", "file": "calc.py", "pairs": [["return x * 2", "return x * 3"]], "test": "python no_such_test.py"}])
check("manual mutation — when the test does not pass before breaking anything it is not counted (baseline failed · exit code 2 · killed 0) · the evidence is unknown (not recorded as pass)",
      code_ == 2 and "baseline failed 1" in out_ and "killed 0" in out_ and (manual_evidence() or [{}])[-1].get("result") == "unknown", (out_, manual_evidence()[-1:]))
code_, out_ = manual([{"name": "missing text", "file": "calc.py", "pairs": [["return x * 99", "x"]], "test": "A"}, {"name": "text appearing twice", "file": "calc.py", "pairs": [["return", "x"]], "test": "A"}])
check("manual mutation — when the old text appears 0 times · several times nothing is broken and it is not replaced (exit code 2)", code_ == 2 and "not replaced 2" in out_ and "0 times" in out_ and "killed 0" in out_, out_)
code_, out_ = manual([{"name": "a replacement with a typo", "file": "calc.py", "pairs": [["return x * 2", "return x * * 2"]], "test": "A"}])
check("manual mutation — a replacement that is not valid Python is not replaced (exit code 2 · killed 0): the module would not import, every test would fail, and that used to be "
      "counted as killed and recorded as a pass", code_ == 2 and "not replaced 1" in out_ and "killed 0" in out_ and "the replacement is not valid Python" in out_, out_)
check("… and the evidence of that run is not a pass", (manual_evidence() or [{}])[-1].get("result") != "pass", manual_evidence()[-1:])
(repo.parent / "manual_list.json").write_text(json.dumps([{"name": "one flat pair", "file": "calc.py", "pairs": ["return x * 2", "return x * 3"], "test": "A"}]), encoding="utf-8")
flat_code, flat_out = run_tool("manual_mutate.py", "--repo", repo, "--list", repo.parent / "manual_list.json")
check("manual mutation — `\"pairs\": [\"old\", \"new\"]` (one flat pair) is a malformed list (exit code 2 · the message), not a traceback",
      flat_code == 2 and "malformed" in flat_out and "Traceback" not in flat_out, flat_out)
(repo.parent / "manual_list.json").write_text("[]", encoding="utf-8")
check("manual mutation — an empty list · a malformed list is exit code 2 (0 spots do not end as \"all killed\")", run_tool("manual_mutate.py", "--repo", repo, "--list", repo.parent / "manual_list.json")[0] == 2)
check("after manual mutation the original repository is as it was", "return x * 2" in (repo / "calc.py").read_text(encoding="utf-8"))

# ── per case: the `# case:` pieces of a test file are picked and run, and the test list expands into one test per case ──
CASE_TEST = ("import sys\nsys.path.insert(0, r'%s')\nimport _harness\nfails = []\n\n\ndef finish():\n    print('failures: %%d' %% len(fails))\n    return 1 if fails else 0\n\n\n"
             "raise SystemExit(_harness.run_cases(__file__, globals(), finish))\n# case: alpha\nprint('  ok  alpha')\n# case: beta\nfails.append(1)\nprint('  FAIL  beta')\n" % Path(__file__).resolve().parent)
(repo / "test_cases.py").write_text(CASE_TEST, encoding="utf-8")


def cases(*args):
    done_ = subprocess.run([sys.executable, str(repo / "test_cases.py")] + list(args), capture_output=True, text=True, encoding="utf-8", errors="replace",
                           env=dict(os.environ))
    return done_.returncode, done_.stdout


check("case list — `--list-cases` prints the names one per line after the header line", cases("--list-cases") == (0, "# case list\nalpha\nbeta\n"), cases("--list-cases"))
code_, out_ = cases("--case", "alpha")
check("only the picked case runs (alpha — pass · no failure line of beta · exit code 0)", code_ == 0 and "ok  alpha" in out_ and "beta" not in out_ and "failures: 0" in out_, (code_, out_))
code_, out_ = cases()
check("without picking a case all run (exit code 1 — beta fails)", code_ == 1 and "ok  alpha" in out_ and "FAIL  beta" in out_ and "failures: 1" in out_, (code_, out_))
check("an unknown case name · --case without a name runs nothing and gives exit code 2 (it does not run 0 cases and end as a pass)", cases("--case", "missing")[0] == 2 and "  ok  " not in cases("--case", "missing")[1]
      and cases("--case")[0] == 2, (cases("--case", "missing"), cases("--case")))
(repo / "test_cases_duplicate.py").write_text(CASE_TEST.replace("# case: beta", "# case: alpha"), encoding="utf-8")
done_ = subprocess.run([sys.executable, str(repo / "test_cases_duplicate.py")], capture_output=True, text=True, encoding="utf-8", errors="replace")
check("duplicate case names are not run and give exit code 2", done_.returncode == 2 and "duplicate case names" in done_.stdout, done_.stdout)
(repo / "test_cases_duplicate.py").unlink()
original_list = (repo / ".oqa" / "tests.json").read_text(encoding="utf-8")
(repo / ".oqa" / "tests.json").write_text(json.dumps(TESTS + [{"name": "cs", "command": "python test_cases.py", "cases": True, "exclusive": True}], ensure_ascii=False), encoding="utf-8")
expanded = _impact0.test_list(repo)
check("test list — a test with \"cases\": true becomes one test per case (name \"cs:alpha\" · command \"… --case alpha\"), its other fields (exclusive) stay · the other tests stay as they are",
      [t["name"] for t in expanded] == ["A", "B", "C", "cs:alpha", "cs:beta"]
      and expanded[3] == {"name": "cs:alpha", "command": "python test_cases.py --case alpha", "exclusive": True, "case_base": "python test_cases.py", "case_name": "alpha"}, expanded)
(repo / ".oqa" / "tests.json").write_text(json.dumps(TESTS + [{"name": "empty", "command": "python test_double.py", "cases": True}], ensure_ascii=False), encoding="utf-8")
try:
    _impact0.test_list(repo)
    check("a test whose case list cannot be obtained (a file without case markers) stops", False)
except SystemExit as stopped:
    check("a test whose case list cannot be obtained (a file without case markers) stops — it does not expand to 0 cases and silently vanish", "could not get the case list" in str(stopped), str(stopped))
(repo / ".oqa" / "tests.json").write_text(original_list, encoding="utf-8")
(repo / "test_cases.py").unlink()

# ── the original is run once ──
baseline_path = repo.parent / "baseline.json"
baseline_path.write_text(json.dumps({"A": "fail", "B": "pass", "C": "pass"}), encoding="utf-8")
code, out, failed_base = mutate_run("--map", repo / ".oqa" / "impact-map.json", "--tests-file", tests_path, "--baseline-results", baseline_path)
check("when the baseline results contain a failure nothing is rerun and the baseline fails (A would pass if run again — a failure already recorded is not turned into a pass by a rerun) · test runs 0",
      code == 2 and failed_base.get("baseline") == "fail" and failed_base.get("test_runs") == 0 and "A" in failed_base.get("basis", ""), failed_base)
baseline_path.write_text(json.dumps({"A": "pass", "B": "not in list", "C": "pass"}), encoding="utf-8")
code, out, given = mutate_run("--map", repo / ".oqa" / "impact-map.json", "--tests-file", tests_path, "--baseline-results", baseline_path)
mutant_runs = sum(r["ran"] for r in given.get("mutants", []))      # the number of tests actually run — it stops on a kill, so it can be fewer than selected
check("every test the mutants use runs once on the unmutated copy, whether or not its result was given (A given as pass · B — \"not in list\" is not a result · D which is not in the map) — test runs = mutant runs + 3 · C, which no mutant uses, is not run",
      given.get("baseline") == "pass" and given.get("test_runs") == mutant_runs + 3 and given.get("baseline_runs") == 3 and given.get("baseline_results") == {"A": "pass", "B": "pass", "D": "pass"},
      (given.get("test_runs"), mutant_runs, given.get("baseline_runs"), given.get("baseline_results")))
(repo / "test_slow.py").write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
slow_tests_path = repo.parent / "tests_slow.json"
slow_tests_path.write_text(json.dumps(TESTS + [{"name": "C", "command": "python test_slow.py"}], ensure_ascii=False), encoding="utf-8")
json_path = repo.parent / "slow.json"
code, out = run_tool("mutate.py", "--repo", repo, "--files", "fresh.py", "--range", "all", "--test", "python test_slow.py", "--json", json_path, "--timeout", "2",
                     "--map", repo / ".oqa" / "impact-map.json", "--tests-file", slow_tests_path)
slow = json.loads(json_path.read_text(encoding="utf-8")) if json_path.is_file() else {}
check("a test that times out on the original is a baseline failure (not laid down as a pass)", code == 2 and slow.get("baseline") == "fail", slow)
(repo / "test_slow.py").unlink()

# ── the pattern of one test must not leak into the next · failures outside the mutants · timeouts only on the mutant ──
# when the test after a test with its own fail pattern is checked with that pattern, a failure of the default pattern is missed
(repo / "test_strict.py").write_text("import calc\nprint('ok' if calc.double(3) == 6 else 'FAIL: double')\n", encoding="utf-8")
pattern_tests_path = repo.parent / "tests_pattern.json"
pattern_tests_path.write_text(json.dumps([{"name": "0E", "command": "python test_strict.py", "fail_pattern": "(?m)^XYZonly"}, {"name": "A", "command": "python test_double.py"}], ensure_ascii=False), encoding="utf-8")
# the name "0E" — tests run in name order. The test with its own pattern must run before A for the overwrite to show (a name that sorts after "A", such as "E", would let A run first and the overwrite would go unnoticed)
json_path = repo.parent / "pattern.json"
run_tool("mutate.py", "--repo", repo, "--files", "calc.py", "--range", "all", "--test", "python test_strict.py", "--test", "python test_double.py", "--json", json_path, "--timeout", "60",
         "--map", repo / ".oqa" / "impact-map.json", "--tests-file", pattern_tests_path)
pattern_result = json.loads(json_path.read_text(encoding="utf-8")) if json_path.is_file() else {}
double_pattern_rows = [r for r in pattern_result.get("mutants", []) if r["line"] == 4]
# the failure mark is not written literally in the text of this line — a passing run of this test must not be read as a failure by the tools
check("the test (A) after a test with its own pattern (0E) is checked with the default pattern — the double mutants are killed by A's default failure mark",
      double_pattern_rows and all(r["verdict"] == "killed" for r in double_pattern_rows), double_pattern_rows)
# when the given results contain even one failure (even of a test the mutants do not use) the baseline fails
baseline_path.write_text(json.dumps({"A": "pass", "B": "pass", "C": "fail", "D": "pass"}), encoding="utf-8")
code, out, outside_fail = mutate_run("--map", repo / ".oqa" / "impact-map.json", "--tests-file", tests_path, "--baseline-results", baseline_path)
check("a test the mutants do not use (C — does not pass through calc.py) failing on the original is a baseline failure too",
      code == 2 and outside_fail.get("baseline") == "fail" and "C" in outside_fail.get("basis", ""), outside_fail)
# when the original passes and only the mutant times out, that mutant is "crashed" (not killed)
(repo / "halt.py").write_text("def waiting():\n    return 0\n", encoding="utf-8")
(repo / "test_halt.py").write_text("import time, halt\nwhile halt.waiting() != 0:\n    time.sleep(1)\nprint('  ok  halt')\n", encoding="utf-8")
halt_tests_path = repo.parent / "tests_halt.json"
halt_tests_path.write_text(json.dumps([{"name": "S", "command": "python test_halt.py"}], ensure_ascii=False), encoding="utf-8")
json_path = repo.parent / "halt.json"
code, out = run_tool("mutate.py", "--repo", repo, "--files", "halt.py", "--range", "all", "--test", "python test_halt.py", "--json", json_path, "--timeout", "3",
                     "--map", repo / ".oqa" / "impact-map.json", "--tests-file", halt_tests_path)
halted = json.loads(json_path.read_text(encoding="utf-8")) if json_path.is_file() else {}
check("when the original passes and only the mutant times out it is \"crashed\" (a file not in the map · the test S not in the map)", halted.get("baseline") == "pass"
      and any(r["verdict"] == "crashed" for r in halted.get("mutants", [])) and not any(r["verdict"] == "killed" for r in halted.get("mutants", [])), halted)
import time as _time  # noqa: E402
_time.sleep(1)
leftover = subprocess.run(["powershell", "-NoProfile", "-Command",
                           "(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -match 'test_halt' } | Measure-Object).Count"],
                          capture_output=True, text=True, encoding="utf-8", errors="replace").stdout.strip() if os.name == "nt" else "0"
check("a test that timed out is ended as a whole process group — 0 leftover \"test_halt\" processes (ending only the shell left Python behind and the wait hung)", leftover == "0", leftover)
if leftover not in ("0", ""):
    subprocess.run(["powershell", "-NoProfile", "-Command", "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -match 'test_halt' } | ForEach-Object { taskkill /F /T /PID $_.ProcessId }"],
                   capture_output=True)

# ── traceability — results received in advance are not run again ──
(repo / ".oqa" / "specs").mkdir(parents=True)
spec = repo / ".oqa" / "specs" / "spec.md"
spec.write_text("# Spec\n\n## Expected results\n- E1 double is right\n", encoding="utf-8")
(repo / "test_double.py").write_text("# verifies: spec/E1\n" + TEST_A, encoding="utf-8")
not_given = traceability.trace(repo, spec, run_tests=True)
taken = traceability.trace(repo, spec, run_tests=True, known_results={"A": "pass"})
check("traceability does not rerun a result received in advance (A) (tests run 1 → 0, same verdict)", not_given["tests_run"] == 1 and taken["tests_run"] == 0
      and [i["verdict"] for i in taken["items"]] == [i["verdict"] for i in not_given["items"]] == ["pass"], (not_given, taken))

# ── the verdict of report.py and the report text ──
fake = {"impact": {"results": {}, "gaps": {}, "changed": []}, "mutation": {"baseline": "pass", "generated": 3, "counts": {"killed": 2, "survived": 0, "crashed": 0, "gap": 1}, "mutants": []},
        "spec": {"status": "present", "items": [{"id": "E1", "text": "x", "tests": ["A"], "files": [], "verdict": "pass"}]}}
code_, incomplete_, unknown_ = report.judge(fake)
check("with a gap mutant the verdict of the report is incomplete (exit code 1) — \"mutants no test passed through\"", code_ == 1 and any("mutants no test passed through 1" in s for s in incomplete_), incomplete_)
git(repo, "add", "-A")
# commit and record again so that the map is not stale, then run report.py with one edited line
git(repo, "commit", "-qm", "second")
run_tool("impact.py", "record", "--repo", repo)
(repo / "calc.py").write_text((repo / "calc.py").read_text(encoding="utf-8").replace("return x * 2", "return 2 * x"), encoding="utf-8")
read_texts, original_read = [], report.read_equivalents
report.read_equivalents = lambda text: (read_texts.append(text), original_read(text))[1]
try:
    with contextlib.redirect_stdout(io.StringIO()):
        collected = report.collect(repo, spec, max_=6)
finally:
    report.read_equivalents = original_read
(spec.parent / "equivalent_test.md").write_text("## What\n- equivalent mutant: a.py:1 x — example text\n\n## Results\n- equivalent mutant: b.py:2 y — reason\n", encoding="utf-8")
check("report.collect reads the equivalent mutants only from the \"## Results\" section of that spec, after counting the mutants", len(read_texts) == 1
      and read_texts[0] == (report.section(spec.read_text(encoding="utf-8-sig"), "Results") or "")
      and report.read_equivalents(report.section((spec.parent / "equivalent_test.md").read_text(encoding="utf-8"), "Results") or "") == [("b.py", 2, "y", "reason")], read_texts[:1])
(spec.parent / "equivalent_test.md").unlink()
mutation = collected.get("mutation", {})
mutant_runs = sum(r["ran"] for r in mutation.get("mutants", []))
used_tests = {n for r in mutation.get("mutants", []) for n in r["tests"]}
outside_impact = used_tests - set((collected.get("impact") or {}).get("results") or {})
check("report.py hands the impact results to mutation, and mutation still runs every test its mutants use once on the unmutated copy — the test runs of the mutation step = the runs on mutants + one run for each used test (%s, of which without an impact result: %s) · %s"
      % (sorted(used_tests), sorted(outside_impact), mutation.get("test_runs")),
      mutation.get("baseline") == "pass" and mutant_runs > 0 and mutation.get("test_runs") == mutant_runs + len(used_tests) and mutation.get("baseline_runs") == len(used_tests) and len(outside_impact) < len(used_tests),
      (mutation.get("test_runs"), mutant_runs, sorted(used_tests), sorted(outside_impact)))
printed = io.StringIO()
with contextlib.redirect_stdout(printed):
    code_ = report.main(["--repo", str(repo), "--spec", str(spec), "--no-write", "--max", "6"])
text_ = printed.getvalue()
check("the report shows the number of test runs (impact tests · mutation · trace) and the elapsed time", "- Test runs:" in text_ and "elapsed" in text_, text_[-600:])
check("in one report.py run the trace does not run again (trace 0)", "trace 0)" in text_, text_[-600:])
check("when the map is not stale the map is handed to mutation (no \"all\" in the test selection)", "test selection" in text_ and "all" not in text_.split("test selection")[1].splitlines()[0], text_[-600:])
git(repo, "commit", "-qam", "third")                              # HEAD moves, so the map becomes stale
(repo / "calc.py").write_text((repo / "calc.py").read_text(encoding="utf-8").replace("return 2 * x", "return x + x"), encoding="utf-8")
printed = io.StringIO()
with contextlib.redirect_stdout(printed):
    code_ = report.main(["--repo", str(repo), "--spec", str(spec), "--no-write", "--max", "6"])
text_ = printed.getvalue()
check("when the map is stale line level is not used (selection \"all\") and the verdict keeps the unknown \"the impact map is stale\"", "all" in text_.split("test selection")[-1].splitlines()[0]
      and "the impact map is stale" in text_ and code_ != 0, text_[-700:])

# ── gap of added lines — when confirmed by other evidence it is not counted as incomplete ──
ADDED = "3 added line(s) — added after the map was recorded, so the map cannot say which tests pass through them; they are settled later in the same report by the mutants on those lines"


def shape(gaps, mutant_rows=(), selected=None, results_=None, ranges=None):
    counts = {k: sum(1 for r in mutant_rows if r["verdict"] == k) for k in ("killed", "survived", "crashed", "gap")}
    ranges = {f: [[1, 3]] for f, reason in gaps.items() if report.ADDED_LINES.match(reason)} if ranges is None else ranges
    # unless a check says otherwise one selected test ("T") ran and passed — with no test run at all the verdict is "cannot judge" whatever the rest says (tested separately below)
    return {"impact": {"gaps": gaps, "added_ranges": ranges, "selected": selected or {}, "results": {"T": "pass"} if results_ is None else results_, "changed": list(gaps)},
            # a shape without mutants is "no changed Python code" (only test files changed) — generated 0 is unknown now and is tested separately
            "mutation": {"baseline": "pass", "generated": len(mutant_rows), "counts": counts, "mutants": [dict(r) for r in mutant_rows]} if mutant_rows else {"none": "no changed Python code"},
            "spec": {"status": "present", "items": []}}


def mutant_row(file, verdict_, line=1, basis=""):
    return {"file": file, "line": line, "kind": "x", "verdict": verdict_, "basis": basis}


code_, inc_, unk_ = report.judge(shape({"scripts/a.py": ADDED}, [mutant_row("scripts/a.py", "killed"), mutant_row("scripts/a.py", "killed")]))
check("gap of added lines — both mutants of that file killed → not incomplete (all passed)", code_ == 0 and not inc_, (code_, inc_))
traced_ = shape({"scripts/a.py": ADDED}, [mutant_row("scripts/a.py", "killed"), mutant_row("scripts/a.py", "killed")])
traced_["spec"]["items"] = [{"id": "E1", "text": "first", "verdict": "pass", "tests": []}, {"id": "E2", "text": "second", "verdict": "missing", "tests": []}]
code_, inc_, unk_ = report.judge(traced_)
check("an expected result whose verdict is not pass (E2 missing) makes the report incomplete (exit code 1) and is named — the passing one is not, and nothing else is wrong",
      code_ == 1 and inc_ == ["E2 missing"] and not unk_, (code_, inc_, unk_))
traced_["spec"]["items"] = [{"id": "E1", "text": "first", "verdict": "duplicate number", "tests": []}, {"id": "E1", "text": "second", "verdict": "duplicate number", "tests": []}]
code_, inc_, unk_ = report.judge(traced_)
check("expected results that share a number are cannot judge (exit code 2) — and the traceability evidence is unknown, not pass",
      code_ == 2 and not inc_ and len(unk_) == 2 and report.check_results(traced_)["Requirements Traceability"][0] == "unknown", (code_, inc_, unk_))
code_, inc_, unk_ = report.judge(shape({"scripts/a.py": ADDED}, [mutant_row("scripts/a.py", "killed"), mutant_row("scripts/a.py", "survived")]))
check("gap of added lines — one mutant survived → not confirmed, and listed once: the survivor (rule changed — it used to be listed twice, as \"gap scripts/a.py\" and as survived mutants)",
      code_ == 1 and inc_ == ["survived mutants 1"] and report.confirmed_gaps(shape({"scripts/a.py": ADDED}, [mutant_row("scripts/a.py", "killed"), mutant_row("scripts/a.py", "survived")])) == {}, inc_)
code_, inc_, unk_ = report.judge(shape({"scripts/a.py": ADDED}, [mutant_row("scripts/a.py", "killed"), mutant_row("scripts/a.py", "crashed")]))
check("gap of added lines — one mutant crashed → the gap stays (it was not killed)", "gap scripts/a.py" in inc_, inc_)
code_, inc_, unk_ = report.judge(shape({"scripts/a.py": ADDED}, [mutant_row("scripts/b.py", "killed")]))
check("gap of added lines — no mutant run on that file (not in the sample) → the gap stays", "gap scripts/a.py" in inc_, inc_)
code_, inc_, unk_ = report.judge(shape({"scripts/a.py": "no test passed through changed lines [3, 4]"}, [mutant_row("scripts/a.py", "killed")]))
check("a \"no test passed through changed lines\" gap of a code file is lifted when every mutant on those lines is killed — rule changed: it used to stay whatever the tests did,"
      " so a file no recorded test covered could never be changed together with its new test", code_ == 0 and not inc_, (code_, inc_))
code_, inc_, unk_ = report.judge(shape({"scripts/a.py": "no test passed through changed lines [3, 4]"}, [mutant_row("scripts/b.py", "killed")]))
check("… and it stays when no mutant ran on that file (only those of another file)", "gap scripts/a.py" in inc_, inc_)
code_, inc_, unk_ = report.judge(shape({"tests/a_test.py": ADDED}, selected={"a": ["tests/a_test.py"]}, results_={"a": "pass"}))
check("gap of added lines — a test file is not incomplete when the tests passing through that file pass", code_ == 0 and not inc_, (code_, inc_))
code_, inc_, unk_ = report.judge(shape({"tests/a_test.py": ADDED}, selected={"a": ["tests/a_test.py"]}, results_={"a": "fail"}))
check("gap of added lines — when the test passing through the test file fails the gap stays", "gap tests/a_test.py" in inc_, inc_)
code_, inc_, unk_ = report.judge(shape({"tests/a_test.py": ADDED}, selected={"b": ["scripts/a.py"]}, results_={"b": "pass"}))
check("gap of added lines — when no selected test passes through the test file the gap stays", "gap tests/a_test.py" in inc_, inc_)
text_ = report.report_text(dict(shape({"scripts/a.py": ADDED, "scripts/c.py": ADDED}, [mutant_row("scripts/a.py", "killed")]), time="t", HEAD="h", range="working", fingerprint="f"))
check("report text — a confirmed file goes on the \"added lines — confirmed by other evidence\" line, an unconfirmed one on the \"Gaps\" line", "confirmed by other evidence: scripts/a.py" in text_
      and "- Gaps: scripts/c.py" in text_ and "**Verdict: incomplete 1**" in text_, text_[:600])

# ── only mutants inside the added line range · equivalent mutants · generated 0 · showing crashed ──
A = "scripts/a.py"
code_, inc_, unk_ = report.judge(shape({A: ADDED}, [mutant_row(A, "killed", line=50)]))
check("a killed mutant on an edited line (50) outside the added lines (1~3) leaves the gap of added lines (one sample must not lift the gap of the whole file)", "gap scripts/a.py" in inc_, inc_)
code_, inc_, unk_ = report.judge(shape({A: ADDED}, [mutant_row(A, "killed", line=3), mutant_row(A, "survived", line=50)]))
check("the mutant inside the added lines is killed and the one outside (50) survives → the gap is lifted and only the survived mutant is incomplete", inc_ == ["survived mutants 1"], inc_)
code_, inc_, unk_ = report.judge(shape({A: ADDED}, [mutant_row(A, "killed")], ranges={}))
check("when the added line range is not received (a result of the old shape) nothing is confirmed — the gap stays", "gap scripts/a.py" in inc_, inc_)
code_, inc_, unk_ = report.judge(shape({A: ADDED}, [mutant_row(A, "killed", line=2)], ranges={A: [[1, 3], [10, 14]]}))
check("when nothing was thinned out (every generated mutant ran) an added hunk without a mutant (10~14) is read as lines that hold nothing to mutate — it does not block", code_ == 0 and not inc_, (code_, inc_))
code_, inc_, unk_ = report.judge(shape({A: ADDED}, [mutant_row(A, "gap", line=12), mutant_row(A, "killed", line=2)], ranges={A: [[1, 3], [10, 14]]}))
check("when a mutant inside an added hunk is a \"gap\" (no test passes through) the gap of added lines stays", "gap scripts/a.py" in inc_, inc_)
text_ = report.report_text(dict(shape({A: ADDED}, [mutant_row(A, "killed", line=2)], ranges={A: [[1, 3], [10, 13]]}), time="t", HEAD="h", range="working", fingerprint="f"))
check("the confirmation basis states the number of added hunks · the number of hunks a mutant reached", "mutants in 1 of 2 added hunk(s)" in text_, text_[:500])

# a new code file is confirmed too when every mutant of that file is killed (it has no lines in the map, so it was always a gap)
NEW_FILE = "new file — no test passed through it"
code_, inc_, unk_ = report.judge(shape({A: NEW_FILE}, [mutant_row(A, "killed", line=5), mutant_row(A, "killed", line=90)]))
check("new code file — when every mutant of that file is killed it is not counted as a gap", code_ == 0 and not inc_, (code_, inc_))
code_, inc_, unk_ = report.judge(shape({A: NEW_FILE}, [mutant_row(A, "killed", line=5), mutant_row(A, "survived", line=90)]))
check("new code file — when even one survives it is not confirmed; the survivor is the one entry (rule changed — no second \"gap scripts/a.py\")",
      code_ == 1 and inc_ == ["survived mutants 1"] and report.gap_states(shape({A: NEW_FILE}, [mutant_row(A, "killed", line=5), mutant_row(A, "survived", line=90)]))[A][0] == "survived", inc_)
shape_ = shape({A: NEW_FILE}, [mutant_row(A, "killed", line=5)])
shape_["mutation"]["generated"] = 30
check("new code file — when thinned out (generated 30 · run 1) the gap stays", "gap scripts/a.py" in report.judge(shape_)[1], report.judge(shape_)[1])
code_, inc_, unk_ = report.judge(shape({A: NEW_FILE}, [mutant_row("scripts/b.py", "killed")]))
check("new code file — when that file has no mutant (only those of another file) the gap stays", "gap scripts/a.py" in inc_, inc_)
INIT = "pkg/__init__.py"
shape_ = shape({INIT: NEW_FILE}, [mutant_row("scripts/b.py", "killed")])
shape_["mutation"]["generated_by_file"] = {"scripts/b.py": 1, INIT: 0}
code_, inc_, unk_ = report.judge(shape_)
check("(before) a new file without any mutant that mutate.py does not call inert: a gap and \"no mutant could be generated\"",
      code_ == 1 and inc_ == ["gap " + INIT] and any("no mutant could be generated for " + INIT in u for u in unk_), (code_, inc_, unk_))
shape_["mutation"]["inert_files"] = [INIT]
code_, inc_, unk_ = report.judge(shape_)
check("a new file that is inert as a whole (an empty `__init__.py` of a new package) is confirmed as \"nothing to mutate\" and is not an unmeasured file — the report can pass",
      code_ == 0 and not inc_ and not unk_ and report.gap_states(shape_)[INIT] == ("confirmed", "new file with nothing to mutate (empty, or only imports · docstrings · bare definitions)")
      and report.check_results(shape_)["Mutation Test"][0] == "pass", (code_, inc_, unk_, report.gap_states(shape_)))
shape_["mutation"]["generated"] = 30
check("… not when mutants were left out by --max (nothing is confirmed then)", report.gap_states(shape_)[INIT][0] == "open", report.gap_states(shape_))
code_, inc_, unk_ = report.judge(shape({"tests/new_test.py": NEW_FILE}, [mutant_row(A, "killed")]))
check("the \"new file\" gap of a new test file is not lifted by this rule (code files only) — rule changed: a new test file that no listed test ran is no longer a bare \"gap\","
      " it is cannot judge with the \"not listed in .oqa/tests.json\" message, and still not confirmed",
      "gap tests/new_test.py" not in inc_ and [u for u in unk_ if u.startswith("tests/new_test.py looks like a test file that is not listed in .oqa/tests.json")]
      and "tests/new_test.py" not in report.confirmed_gaps(shape({"tests/new_test.py": NEW_FILE}, [mutant_row(A, "killed")])), (inc_, unk_))
text_ = report.report_text(dict(shape({A: NEW_FILE}, [mutant_row(A, "killed", line=5)]), time="t", HEAD="h", range="working", fingerprint="f"))
check("the report states the confirmation basis of the new file", "scripts/a.py (new file — all 1 mutant(s) of that file killed)" in text_, text_[:500])

equivalents = report.read_equivalents("## Results\n- equivalent mutant: scripts/a.py:2 x — that value is set again right below\n- equivalent mutant: scripts/a.py:9 x\n- equivalent mutant: scripts\\b.py:4 True ↔ False — reason\n")
check("reading equivalent mutants — only lines with a reason (file · line · kind · reason), a backslash path becomes /",
      equivalents == [("scripts/a.py", 2, "x", "that value is set again right below"), ("scripts/b.py", 4, "True ↔ False", "reason")], equivalents)
shape_ = shape({A: ADDED}, [mutant_row(A, "killed", line=1), mutant_row(A, "survived", line=2)])
report.mark_equivalents(shape_["mutation"], equivalents)
code_, inc_, unk_ = report.judge(shape_)
check("a survived mutant declared equivalent in the spec is not counted as incomplete and does not block the confirmation of added lines (all passed)",
      code_ == 0 and not inc_ and shape_["mutation"]["counts"].get("equivalent") == 1, (code_, inc_, shape_["mutation"]["counts"]))
text_ = report.report_text(dict(shape_, time="t", HEAD="h", range="working", fingerprint="f"))
check("the report shows the number of equivalents and that line · reason", "equivalent (declared in the spec) 1" in text_ and "- equivalent scripts/a.py:2 x — that value is set again right below" in text_, text_[-400:])
shape_ = shape({A: ADDED}, [mutant_row(A, "killed", line=1), mutant_row(A, "survived", line=3)])
report.mark_equivalents(shape_["mutation"], equivalents)
check("a survived mutant on a line not declared equivalent (3) stays incomplete", "survived mutants 1" in report.judge(shape_)[1], report.judge(shape_)[1])
shape_ = shape({A: ADDED}, [mutant_row(A, "killed", line=2)])
report.mark_equivalents(shape_["mutation"], equivalents)
check("a killed mutant stays killed even when declared equivalent", shape_["mutation"]["mutants"][0]["verdict"] == "killed" and not shape_["mutation"]["counts"].get("equivalent"), shape_["mutation"])
shape_ = shape({A: ADDED}, [mutant_row(A, "survived", line=2)])
report.mark_equivalents(shape_["mutation"], equivalents)
check("with only equivalents and not a single killed mutant the gap of added lines stays (nothing was confirmed)", "gap scripts/a.py" in report.judge(shape_)[1], report.judge(shape_)[1])

# two survived mutants of the same kind on one line are not taken out by one equivalent line · unmatched equivalent lines are shown in the report · when thinned out a small hunk no mutant reached is a gap too
shape_ = shape({A: ADDED}, [mutant_row(A, "killed", line=1), mutant_row(A, "survived", line=2), mutant_row(A, "survived", line=2)])
report.mark_equivalents(shape_["mutation"], equivalents)
check("with two survived mutants of the same line · same kind neither is taken out as equivalent (survived mutants 2)", "survived mutants 2" in report.judge(shape_)[1]
      and any("not excluded: 2 survived mutants" in s for s in shape_["mutation"].get("equivalent_unmatched", [])), (report.judge(shape_)[1], shape_["mutation"].get("equivalent_unmatched")))
text_ = report.report_text(dict(shape_, time="t", HEAD="h", range="working", fingerprint="f"))
check("unmatched equivalent lines (two, so not excluded · no matching mutant) are shown in the report",
      "equivalent line of the spec not matched: scripts/a.py:2 x — not excluded: 2 survived mutants of the same line" in text_
      and "equivalent line of the spec not matched: scripts/b.py:4 True ↔ False — no matching survived mutant" in text_, text_[-500:])
shape_ = shape({A: ADDED}, [mutant_row(A, "killed", line=2)], ranges={A: [[1, 3], [10, 13]]})
shape_["mutation"]["generated"] = 40
check("when thinned out (generated 40 · run 1) a 4-line hunk no mutant reached was not confirmed either — the gap stays", "gap scripts/a.py" in report.judge(shape_)[1], report.judge(shape_)[1])
many = [mutant_row(A, "survived", line=30 + n) for n in range(20)] + [mutant_row(A, "survived", line=2)]
shape_ = shape({A: ADDED}, many)
report.mark_equivalents(shape_["mutation"], equivalents)
text_ = report.report_text(dict(shape_, time="t", HEAD="h", range="working", fingerprint="f"))
check("with many survived lines the equivalent line is still shown uncut, and the number of cut lines (4 more) is stated", "- equivalent scripts/a.py:2 x — " in text_ and "… and 4 more line(s)" in text_, text_[-300:])

zero_shape = shape({})
zero_shape["mutation"] = {"baseline": "not run", "generated": 0, "mutants": []}
code_, inc_, unk_ = report.judge(zero_shape)
check("changed code but 0 mutants generated → cannot judge (not left as a pass)", code_ == 2 and any("generated 0" in s for s in unk_), (code_, unk_))
zero_shape["mutation"] = {"none": "no changed Python code"}
check("not run because there is no changed Python code (none) is not unknown", report.judge(zero_shape)[0] == 0, report.judge(zero_shape))
text_ = report.report_text(dict(shape({}, [mutant_row(A, "crashed", line=7, basis="Traceback: NameError name")]), time="t", HEAD="h", range="working", fingerprint="f"))
check("crashed mutants are shown line by line too (where and why it crashed)", "- crashed scripts/a.py:7 x — Traceback: NameError name" in text_, text_[-300:])

# ── the evidence values report.py records per check (pass · fail · unknown) ──
traced = {"status": "present", "items": [{"id": "E1", "text": "x", "tests": ["A"], "files": [], "verdict": "pass"}]}
good_shape = dict(shape({}, [mutant_row(A, "killed")], results_={"A": "pass"}), spec=traced)
check("check_results — tests pass · mutant killed · E passes → all four pass", {k: v[0] for k, v in report.check_results(good_shape).items()}
      == {"Verification Report": "pass", "Regression Test": "pass", "Mutation Test": "pass", "Requirements Traceability": "pass"}, report.check_results(good_shape))
results_ = report.check_results(dict(shape({A: ADDED}, [mutant_row(A, "survived", line=2)], results_={"A": "fail"}), spec=dict(traced, items=[dict(traced["items"][0], verdict="fail")])))
check("check_results — test fails · added lines whose mutant survived · E fails → all four fail; the Regression basis names the cause (\"added lines not confirmed … mutants on them survived\" — it used to say \"gap\")",
      {v[0] for v in results_.values()} == {"fail"} and "added lines not confirmed scripts/a.py (mutants on them survived)" in results_["Regression Test"][1] and "gap " not in results_["Regression Test"][1], results_)
unknown_shape = dict(shape({}, [mutant_row(A, "crashed")], results_={}), spec={"status": "no expected results section", "items": []})
code_, inc_, unk_ = report.judge(shape({A: ADDED}, [mutant_row(A, "killed"), mutant_row(A, "killed")], results_={}))
check("with no test run at all, killed mutants and a confirmed gap are still not \"all passed\" — cannot judge (\"no test was selected for this change\")",
      code_ == 2 and not inc_ and unk_ == ["no test was selected for this change"], (code_, inc_, unk_))
results_ = report.check_results(unknown_shape)
check("check_results — 0 tests run · crashed · no expected results in the spec → unknown (not laid down as a pass)", {k: v[0] for k, v in results_.items()}
      == {"Verification Report": "unknown", "Regression Test": "unknown", "Mutation Test": "unknown", "Requirements Traceability": "unknown"}, results_)
results_ = report.check_results(dict(shape({}, results_={"A": "pass"}), spec=traced))
check("check_results — when mutation was not run because there is no changed Python source the Mutation line is `done` with that basis, never `pass`"
      " (rule changed: there used to be no line, and a finish line naming Mutation Test could not be met for a test-only change)",
      results_.get("Mutation Test") == ("done", report.NOTHING_TO_MUTATE) and results_["Regression Test"][0] == "pass", results_)
zero_shape2 = dict(shape({}, results_={"A": "pass"}), spec=traced)
zero_shape2["mutation"] = {"baseline": "not run", "generated": 0, "mutants": []}
check("check_results — with generated 0 Mutation is unknown", report.check_results(zero_shape2)["Mutation Test"][0] == "unknown", report.check_results(zero_shape2))
equivalent_shape = dict(shape({}, [mutant_row(A, "killed", line=1), mutant_row(A, "survived", line=2)], results_={"A": "pass"}), spec=traced)
report.mark_equivalents(equivalent_shape["mutation"], equivalents)
check("check_results — when survived is 0 after taking out the equivalent, Mutation passes · the basis states the number of equivalents",
      report.check_results(equivalent_shape)["Mutation Test"] == ("pass", "run 2 · killed 1 · equivalent 1"), report.check_results(equivalent_shape))

# impact.py hands over the current line range of added lines — two lines added give [[n+1, n+2]], an edited (changed) line is not in the ranges
import impact as _impact  # noqa: E402
git(repo, "checkout", "-q", "--", "calc.py")
run_tool("impact.py", "record", "--repo", repo)
original_lines = (repo / "calc.py").read_text(encoding="utf-8").splitlines(keepends=True)
place = next(i for i, line_ in enumerate(original_lines) if "return 2 * x" in line_)          # a line a test passes through — two lines are added right after it
indent = original_lines[place][:len(original_lines[place]) - len(original_lines[place].lstrip())]
(repo / "calc.py").write_text("".join(original_lines[:place + 1]) + indent + "y = 1\n" + indent + "z = 2\n" + "".join(original_lines[place + 1:]), encoding="utf-8")
expected_ranges = [[place + 2, place + 3]]
_changed, _added, added_ranges = _impact.changed_old_lines(repo, "working")
check("changed_old_lines, third value — the current line range of the added hunk (first line · last line)", added_ranges.get("calc.py") == expected_ranges and _added.get("calc.py") == [2], (added_ranges, _added))
impact_path = repo / ".oqa" / "impact_for_test.json"
with contextlib.redirect_stdout(io.StringIO()):
    _impact.select(repo, "working", False, report.mutate.DEFAULT_FAIL_PATTERN, str(impact_path))
selected_result = json.loads(impact_path.read_text(encoding="utf-8"))
impact_path.unlink()
check("the select result carries the added_ranges of a file with an \"added lines\" gap", bool(report.ADDED_LINES.match(selected_result.get("gaps", {}).get("calc.py", "")))
      and selected_result.get("added_ranges") == {"calc.py": expected_ranges}, selected_result)

# when report.main writes the report it leaves one evidence line per check (with the fingerprint measured then). With --no-write no evidence is recorded either
import evidence as _evidence  # noqa: E402
(repo / ".oqa" / "evidence.jsonl").unlink(missing_ok=True)
with contextlib.redirect_stdout(io.StringIO()):
    report.main(["--repo", str(repo), "--spec", str(spec), "--no-write", "--max", "6"])
check("with --no-write the evidence record has no report.py line", not [d for d in _evidence.read(repo) if d.get("tool") == "report.py"], _evidence.read(repo))
handed, original_quiet = [], report.record_quietly
report.record_quietly = lambda *a, **k: (handed.append(a[4] if len(a) > 4 else k.get("fingerprint_value")), original_quiet(*a, **k))[1]
try:
    with contextlib.redirect_stdout(io.StringIO()):
        report.main(["--repo", str(repo), "--spec", str(spec), "--max", "6"])
finally:
    report.record_quietly = original_quiet
check("report.py hands the evidence the fingerprint it measured after running the tests (it is not measured again when recording)",
      len(handed) == 4 and len(set(handed)) == 1 and handed[0] == _evidence.recorded_fingerprint(spec.read_text(encoding="utf-8")), handed)
# when the code changes while the tests run (start · end fingerprints differ) there is no fingerprint · cannot judge — no evidence is recorded with the end fingerprint
alternating, original_fingerprint = iter(["before_fp", "after_fp"]), report.fingerprint
report.fingerprint = lambda *a, **k: next(alternating)
try:
    with contextlib.redirect_stdout(io.StringIO()):
        changed_collected = report.collect(repo, spec, max_=1)
finally:
    report.fingerprint = original_fingerprint
code_, inc_, unk_ = report.judge(changed_collected)
check("report.py — when the code changes during the run the fingerprint is None · cannot judge (unknown carries the reason) · the Verification Report value is unknown",
      changed_collected["fingerprint"] is None and changed_collected["fingerprint_changed"] is True
      and any("the code changed while the tests were running" in s for s in unk_) and code_ != 0 and report.check_results(changed_collected)["Verification Report"][0] != "pass",
      (changed_collected["fingerprint"], code_, unk_))
# when the start fingerprint could not be measured, an end fingerprint alone is not used either — whether the code changed in between is unknown
for head_, sequence in (("the start could not be measured", [None, "after_fp"]), ("the end could not be measured", ["before_fp", None])):
    alternating = iter(sequence)
    report.fingerprint = lambda *a, **k: next(alternating)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            unmeasured = report.collect(repo, spec, max_=1)
    finally:
        report.fingerprint = original_fingerprint
    code_, inc_, unk_ = report.judge(unmeasured)
    check("report.py — %s → fingerprint None · cannot judge (\"could not measure the code fingerprint\") · Verification Report is not pass" % head_,
          unmeasured["fingerprint"] is None and unmeasured["fingerprint_changed"] is False
          and any("could not measure the code fingerprint" in s for s in unk_) and code_ != 0 and report.check_results(unmeasured)["Verification Report"][0] != "pass",
          (unmeasured["fingerprint"], code_, unk_))
fake_section = "## What\n### Verification Report\nfingerprint fake\n\n## Results\ntext\n\n### Verification Report\n\ncounted by report.py · fingerprint real\n"
check("the report section is only the one inside \"## Results\" — the fingerprint of a fake section copied at a line start into an earlier section is not read",
      _evidence.recorded_fingerprint(fake_section) == "real" and _evidence.recorded_fingerprint("## What\n### Verification Report\nfingerprint fake\n\n## Results\ntext\n") is None
      and _evidence.recorded_fingerprint("### Verification Report\n\nfingerprint nosection\n") == "nosection", (_evidence.recorded_fingerprint(fake_section),))
recorded = {d["kind"]: d for d in _evidence.read(repo) if d.get("tool") == "report.py"}
spec_fingerprint = _evidence.recorded_fingerprint(spec.read_text(encoding="utf-8"))
check("when report.py writes the report there is Verification Report · Regression · Mutation · Traceability evidence — the fingerprint equals the one written in the report",
      set(recorded) == {"Verification Report", "Regression Test", "Mutation Test", "Requirements Traceability"} and spec_fingerprint not in (None, "unknown")
      and all(d["fingerprint"] == spec_fingerprint for d in recorded.values()) and spec_fingerprint == _evidence.fingerprint(repo),
      (spec_fingerprint, {k: (v["result"], v["fingerprint"]) for k, v in recorded.items()}))

print("\nfailures: %d" % len(failures))
sys.exit(1 if failures else 0)
