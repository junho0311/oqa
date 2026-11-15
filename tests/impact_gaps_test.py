# -*- coding: utf-8 -*-
"""Checks of the decisions in `scripts/impact.py` that a whole-file mutation run found unguarded.

    python tests/impact_gaps_test.py

Every check calls the function that makes the decision and compares one observable result with an answer fixed in
advance: the content of the map file, what `select` returns, prints and writes, the message of a refused test list,
the text `decode` gives back, what `run_command` / `run_streaming` return for a command that exits non-zero, times
out, prints a failure line or leaves a child process, and (on Windows) what the job object does to the processes in it.
A call that raises is turned into a failed check, so a failure is always a `  FAIL  ` line and never a bare crash.
Checks of lines that only one operating system executes print `  skip  <reason>` on the other one.
"""
import _harness  # noqa: F401,E402 — collects the temp folders in one place for this test and removes it at the end
import contextlib
import io
import json
import locale
import os
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path

for _key in ("PYTHONIOENCODING", "PYTHONUTF8"):
    os.environ.pop(_key, None)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
TOOLS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(TOOLS))
import impact  # noqa: E402
import mutate  # noqa: E402

PATTERN = mutate.DEFAULT_FAIL_PATTERN
WINDOWS = os.name == "nt"
failures = []


def check(name, condition, text=""):
    print(("  ok  " if condition else "  FAIL  ") + name)
    if not condition:
        failures.append(name)
        if text != "":
            print("        " + str(text)[:600].replace("\n", " / "))


def skip(reason):
    print("  skip  " + reason)


def attempt(function, *args, **kwargs):
    """(value, None), or (None, what was raised) — an exception becomes a value a check can fail on"""
    try:
        return function(*args, **kwargs), None
    except (Exception, SystemExit) as error:
        return None, "%s: %s" % (type(error).__name__, error)


def printed(function, *args, **kwargs):
    """(value, what was raised | None, the text the call printed)"""
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        value, error = attempt(function, *args, **kwargs)
    return value, error, buffer.getvalue()


def section(title, body):
    """Run one group of checks; an exception inside the group is one failed check, not the end of the test"""
    print(title)
    try:
        body()
    except Exception:                                        # noqa: BLE001
        check(title + " — the group ran to its end", False, traceback.format_exc()[-600:])


def git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), "-c", "user.email=t@t", "-c", "user.name=t"] + list(args), capture_output=True, check=True)


def new_repo(files):
    root = Path(tempfile.mkdtemp()) / "target"
    root.mkdir()
    git(root, "init", "-q")
    for name, text in dict({".gitignore": ".oqa/\n"}, **files).items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text, encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "first")
    (root / ".oqa").mkdir()
    return root


def write_list(repo, entries):
    (repo / ".oqa" / "tests.json").write_text(json.dumps(entries), encoding="utf-8")


def write_map(repo, tests, **fields):
    map_ = dict({"created": "2026-01-01 00:00", "HEAD": impact.head(repo), "modified": [], "tests": tests}, **fields)
    (repo / ".oqa" / "impact-map.json").write_text(json.dumps(map_), encoding="utf-8")


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


WORK = Path(tempfile.mkdtemp())
SELECT_JSON = WORK / "select.json"
(WORK / "emit.py").write_text("import sys\nprint('to-out')\nsys.stderr.write('to-err\\n')\nsys.exit(3)\n", encoding="utf-8")
(WORK / "emit_then_sleep.py").write_text(
    "import sys, time\nprint('early-out', flush=True)\nsys.stderr.write('early-err\\n')\nsys.stderr.flush()\ntime.sleep(60)\n", encoding="utf-8")
(WORK / "sleeper.py").write_text("import time\ntime.sleep(60)\n", encoding="utf-8")
(WORK / "failing.py").write_text("import time\nprint('  ok  a', flush=True)\nprint('  FAIL  b', flush=True)\ntime.sleep(60)\n", encoding="utf-8")
# starts a child that inherits the output pipes, says its pid and exits at once — the child is what is left behind
(WORK / "leaves.py").write_text(
    "import subprocess, sys\nchild = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(40)'])\nprint(child.pid, flush=True)\n", encoding="utf-8")


def pid_alive(pid):
    if WINDOWS:
        handle = K.OpenProcess(0x00100000, 0, pid)          # SYNCHRONIZE
        if not handle:
            return False
        try:
            return K.WaitForSingleObject(handle, 0) == 258  # WAIT_TIMEOUT: not ended
        finally:
            K.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:                                                     # a killed process that nobody has collected yet is not alive
        return Path("/proc/%d/stat" % pid).read_text().rsplit(")", 1)[1].split()[0] != "Z"
    except (OSError, IndexError):
        return True


def gone_within(pid, seconds):
    limit = time.time() + seconds
    while time.time() < limit:
        if not pid_alive(pid):
            return True
        time.sleep(0.1)
    return not pid_alive(pid)


def force_end(pid):
    if WINDOWS:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)
    else:
        with contextlib.suppress(OSError):
            os.kill(pid, 9)


if WINDOWS:
    import ctypes
    K = ctypes.WinDLL("kernel32")                            # an instance of its own: the declarations below do not touch ctypes.windll.kernel32, which impact.py declares
    K.OpenProcess.restype = ctypes.c_void_p
    K.OpenProcess.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.c_uint]
    K.WaitForSingleObject.restype = ctypes.c_uint
    K.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    K.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint)]
    K.GetHandleInformation.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint)]
    K.CloseHandle.argtypes = [ctypes.c_void_p]


# ───────── test list ─────────
def test_list_checks():
    repo = new_repo({"calc.py": "x = 1\n",
                     "nocases.py": "print('plain output marker')\n",
                     "cases.py": "print('# case list')\nprint('alpha')\nprint('beta')\n"})
    good = [{"name": "a", "command": "python a.py"}, {"name": "b", "command": "python b.py"}]
    write_list(repo, good)
    value, error = attempt(impact.test_list, repo)
    check("a well-formed list comes back entry for entry", value == good, error or value)
    for label, entries in (("an entry without a command (next to a good one)", [good[0], {"name": "b"}]),
                           ("an entry without a name", [{"command": "python a.py"}]),
                           ("an entry that is not an object", ["python a.py"]),
                           ("an object instead of a list", good[0])):
        write_list(repo, entries)
        value, error = attempt(impact.test_list, repo)
        check("%s is refused as a malformed test list" % label, value is None and (error or "").startswith("SystemExit: malformed test list:"), error or value)
    write_list(repo, [good[0], {"name": "a", "command": "python other.py"}, good[1]])
    value, error = attempt(impact.test_list, repo)
    check("two entries with one name are refused and the name is said (results are kept by name: one of the two was never run and passed on the other's result)",
          value is None and (error or "").startswith("SystemExit: malformed test list:") and "the name a is used by more than one entry" in (error or ""), error or value)
    for label, raw in (("a file cut off while being written", b'[\n {\n  "name": "a",\n  "command": "pyth'), ("a file saved as UTF-16", json.dumps(good).encode("utf-16"))):
        (repo / ".oqa" / "tests.json").write_bytes(raw)
        value, error = attempt(impact.test_list, repo)
        check("%s is refused as a malformed test list with the reason — a message, not a traceback" % label,
              value is None and (error or "").startswith("SystemExit: malformed test list:") and "cannot be read as UTF-8 JSON" in (error or ""), error or value)
    write_list(repo, [{"name": "t", "command": "python nocases.py", "cases": True}])
    value, error = attempt(impact.test_list, repo)
    check("a test that prints no case list stops the expansion, and the message carries the exit code and the output it printed instead",
          value is None and (error or "").startswith("SystemExit: could not get the case list (python nocases.py --list-cases")
          and "exit code 0" in (error or "") and "plain output marker" in (error or ""), error or value)
    write_list(repo, [{"name": "t", "command": "python cases.py", "cases": True}])
    value, error = attempt(impact.test_list, repo)
    check("a test with a case list becomes one entry per case",
          [(t["name"], t["command"], t.get("case_base"), t.get("case_name")) for t in value or []]
          == [("t:alpha", "python cases.py --case alpha", "python cases.py", "alpha"), ("t:beta", "python cases.py --case beta", "python cases.py", "beta")]
          and not any("cases" in t for t in value or []), error or value)


section("test_list — malformed lists and case expansion", test_list_checks)


# ───────── decode ─────────
def decode_checks():
    real = locale.getpreferredencoding

    def with_encoding(name, data):
        locale.getpreferredencoding = lambda *args: name
        try:
            return attempt(impact.decode, data)
        finally:
            locale.getpreferredencoding = real
    value, error = with_encoding("cp932", b"plain \xe2\x82\xac")
    check("UTF-8 bytes are read as UTF-8 whatever the legacy encoding is", value == "plain €", error or ascii(value))
    value, error = with_encoding("no-such-encoding", b"\xff FAILED x")
    check("an unknown legacy encoding falls back to UTF-8 with replacement characters (text, not None)", value == "� FAILED x", error or ascii(value))
    # cp932 reads 0x80 as the character U+0080 and 0x81 0x41 as one character: the letter A is swallowed
    value, error = with_encoding("cp932", b"\x80\x81AFAILED")
    check("a lost ASCII letter is noticed even when the text also holds U+0080 (not an ASCII character): every ASCII byte is kept",
          value == "��AFAILED", error or ascii(value))
    value, error = with_encoding("cp932", b"\x80ok")
    check("a 0x80 byte is not counted as an ASCII byte: with no ASCII letter lost the legacy text is kept", value == "\x80ok", error or ascii(value))
    value, error = with_encoding("cp932", b"\x83e\x83X\x83g ok")
    check("legacy text whose second bytes are ASCII letters by design is not kept either (as documented, the ASCII bytes win)",
          value == "�e�X�g ok", error or ascii(value))


section("decode — legacy encodings", decode_checks)


# ───────── run_command · run ─────────
def run_command_checks():
    value, error = attempt(impact.run_command, "python emit.py", WORK, 60, None)
    code, out, err = value or (None, b"", b"")
    check("a command that exits non-zero: exit code 3, stdout and stderr each in its own place",
          code == 3 and out.strip() == b"to-out" and err.strip() == b"to-err", error or value)
    started = time.time()
    value, error = attempt(impact.run_command, "python emit_then_sleep.py", WORK, 3, None)
    code, out, err = value or (0, b"", b"")
    check("a command that times out: exit code None, and what it had printed to stdout and to stderr before is kept",
          error is None and code is None and out.strip() == b"early-out" and err.strip() == b"early-err" and time.time() - started < 12,
          error or (value, time.time() - started))
    value, error = attempt(impact.run, "python sleeper.py", WORK, None, 1)
    check("run() on a timeout gives the pair (None, \"timeout\")", value == (None, "timeout"), error or value)
    value, error = attempt(impact.run, "python emit.py", WORK)
    check("run() joins stdout and stderr after the exit code", value is not None and value[0] == 3 and value[1].split() == ["to-out", "to-err"], error or value)
    started = time.time()
    value, error = attempt(impact.run_command, "python leaves.py", WORK, 25, None)
    code, out, err = value or (None, b"", b"")
    child = int(out.strip()) if out.strip().isdigit() else None
    check("a command that leaves a child holding the output pipe returns at once with its own exit code (not at the time limit)",
          code == 0 and child is not None and time.time() - started < 15, error or (value, time.time() - started))
    check("... and the leftover child is ended", child is not None and gone_within(child, 5), child)
    if child is not None and pid_alive(child):
        force_end(child)


section("run_command — non-zero exit, timeout, leftover child", run_command_checks)


# ───────── run_streaming ─────────
def run_streaming_checks():
    started = time.time()
    value, error = attempt(impact.run_streaming, "python failing.py", WORK, 40, None, PATTERN)
    code, out, err, hit = value or (None, b"", b"", None)
    check("a failure line ends the command on the spot: the line is returned, the exit code is not None, the earlier output is kept",
          hit == "FAIL  b" and code is not None and b"  ok  a" in out and time.time() - started < 25, error or (value, time.time() - started))
    value, error = attempt(impact.run_streaming, "python sleeper.py", WORK, 2, None, PATTERN)
    check("a command that times out without a failure line: (None, no output, no output, no line)", value == (None, b"", b"", None), error or value)


section("run_streaming — failure line and timeout", run_streaming_checks)


# ───────── a process that does not end when its group is told to ─────────
def stubborn_checks():
    seen = {}

    class StubbornJob(impact._Job):
        """Ending the group does nothing, so the started process is still there after the grace period; close() notes
        whether the started process itself was ended by then, and only then ends everything for real"""

        def terminate(self):
            pass

        def close(self):
            try:
                self.proc.wait(timeout=3)
                ended = True
            except subprocess.TimeoutExpired:
                ended = False
            seen.setdefault(threading.current_thread().name, ended)
            super().close()

    results = {}

    def call(name, function, *args):
        results[name] = attempt(function, *args)
    real = impact._Job
    impact._Job = StubbornJob
    try:
        threads = [threading.Thread(target=call, name="command", args=("command", impact.run_command, "python sleeper.py", WORK, 1, None)),
                   threading.Thread(target=call, name="streaming", args=("streaming", impact.run_streaming, "python sleeper.py", WORK, 1, None, PATTERN))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)
    finally:
        impact._Job = real
    check("run_command: a process that outlives the group kill is killed itself before the function returns", seen.get("command") is True, seen)
    check("run_command: ... and the result is (None, no output, no output)", results.get("command") == ((None, b"", b""), None), results.get("command"))
    check("run_streaming: a process that outlives the group kill is killed itself before the function returns", seen.get("streaming") is True, seen)
    check("run_streaming: ... and the result is (None, no output, no output, no line)", results.get("streaming") == ((None, b"", b"", None), None), results.get("streaming"))


section("run_command · run_streaming — the started process survives the group kill (waits the 10 second grace period)", stubborn_checks)


# ───────── record ─────────
CALC = "def grade(score):\n    return 'accepted' if score >= 60 else 'refused'\n"
CALC_TEST = ("import sys\nsys.path.insert(0, '.')\nfrom calc import grade\n"
             "bad = [s for s, a in ((59, 'refused'), (60, 'accepted')) if grade(s) != a]\n"
             "print('failures: %d' % len(bad))\n"
             "sys.exit(1 if bad else 0)\n")


def record_checks():
    repo = new_repo({"calc.py": CALC, "calctest.py": CALC_TEST})
    write_list(repo, [{"name": "calc", "command": "python calctest.py"}])
    map_path = repo / ".oqa" / "impact-map.json"
    value, error, text = printed(impact.record, repo, PATTERN)
    map_ = read_json(map_path) if map_path.is_file() else {}
    check("record on a clean repository returns 0 (the number, not None)", value == 0 and value is not None, error or repr(value))
    check("... prints no warning about modified files", error is None and "warning" not in text and "modified" not in text, text)
    check("... the map holds HEAD, no modified file, the result and the command of the test",
          map_.get("HEAD") == impact.head(repo) and map_.get("modified") == [] and map_.get("results") == {"calc": "pass"}
          and map_.get("commands") == {"calc": "python calctest.py"}, map_)
    lines = map_.get("tests", {}).get("calc", {})
    check("... and the lines the test executed in both files (calc.py 1-2, calctest.py 1-6)",
          lines.get("calc.py") == [1, 2] and lines.get("calctest.py") == [1, 2, 3, 4, 5, 6] and sorted(lines) == ["calc.py", "calctest.py"], lines)
    check("a fresh map is not stale", attempt(impact.stale_reason, map_, repo) == (None, None), attempt(impact.stale_reason, map_, repo))
    check("... a map of another HEAD is", attempt(impact.stale_reason, dict(map_, HEAD="0" * 40), repo) == ("differs from the current HEAD", None))
    check("... and so is any range other than the working tree",
          attempt(impact.stale_reason, map_, repo, "HEAD~1") == ("range HEAD~1 does not line up with the HEAD-based map", None))

    (repo / "calc.py").write_text(CALC.replace("else 'refused'\n", "else 'refused'  # edited\n"), encoding="utf-8")
    value, error, text = printed(impact.select, repo, "working", False, PATTERN, SELECT_JSON)
    check("select after an edit of a recorded line: the test that passed through it, no gap, exit code 0",
          value == 0 and read_json(SELECT_JSON)["selected"] == {"calc": ["calc.py"]} and read_json(SELECT_JSON)["gaps"] == {}, error or text)
    value, error, text = printed(impact.record, repo, PATTERN)
    map_ = read_json(map_path)
    check("record with a modified tracked .py file prints the warning that names it, and still returns 0",
          value == 0 and "warning: recording with 1 modified .py file(s) (calc.py)" in text, error or text)
    check("... the map lists the file, and that is its stale reason",
          map_.get("modified") == ["calc.py"] and attempt(impact.stale_reason, map_, repo) == ("recorded with modified .py files", None), map_.get("modified"))

    (repo / "calc.py").write_text(CALC.replace(">= 60", ">= 61"), encoding="utf-8")
    value, error, text = printed(impact.record, repo, PATTERN)
    check("record with a failing test returns 1, names the test, and the map says fail",
          value == 1 and "1 test(s) failed while recording: calc " in text and read_json(map_path).get("results") == {"calc": "fail"}, error or text)


section("record — return value, warning, map content", record_checks)


# ───────── select ─────────
VALUES = "".join("v%d = %d\n" % (n, n) for n in range(1, 13))


def select_checks():
    repo = new_repo({"values.py": VALUES, "other.py": "x = 1\n", "okay.py": "print('fine')\n"})
    head = impact.head(repo)

    def edit(numbers):
        (repo / "values.py").write_text("".join("v%d = %d\n" % (n, n * (100 if n in numbers else 1)) for n in range(1, 13)), encoding="utf-8")

    def select(run=False, range_="working"):
        value, error, text = printed(impact.select, repo, range_, run, PATTERN, SELECT_JSON)
        return value, text if error is None else text + " / " + error, (read_json(SELECT_JSON) if error is None else {})

    edit({2})
    write_map(repo, {"t1": {"values.py": [1, 2, 3]}})
    code, text, result = select()
    rows = text.splitlines()
    check("a changed line a test passed through: that test is selected, exit code 0", code == 0 and result.get("selected") == {"t1": ["values.py"]} and result.get("gaps") == {}, text)
    check("the first printed line names the map's date and the first seven characters of its HEAD, with no stale note",
          rows[:1] == ["impact map: 2026-01-01 00:00 (HEAD %s)" % head[:7]], rows[:1])
    check("the second line counts the selection", rows[1:2] == ["selected tests 1 / total 1: t1"], rows[1:2])
    check("with only Python files changed no \"unseen\" line is printed, and the unseen list is empty", "unseen" not in text and result.get("unseen") == [] and len(rows) == 2, text)
    write_map(repo, {"t1": {"values.py": [1, 2, 3]}}, HEAD=None)
    code, text, result = select()
    check("a map without a HEAD prints \"?\" for it and is stale",
          text.splitlines()[:1] == ["impact map: 2026-01-01 00:00 (HEAD ?) — **stale: differs from the current HEAD, record again**"] and result.get("map_stale") is True, text)

    write_map(repo, {"t1": {"other.py": [1]}})
    edit(set(range(2, 10)))
    code, text, result = select()
    check("exactly eight changed lines nobody passed through are all listed (no \"...\"), exit code 1",
          code == 1 and result.get("gaps") == {"values.py": "no test passed through changed lines [2, 3, 4, 5, 6, 7, 8, 9]"}
          and "  gap values.py — no test passed through changed lines [2, 3, 4, 5, 6, 7, 8, 9]" in text.splitlines()
          and "selected tests 0 / total 1: none" in text.splitlines(), text)
    edit(set(range(2, 11)))
    code, text, result = select()
    check("nine of them: the first eight and \"...\"",
          code == 1 and result.get("gaps") == {"values.py": "no test passed through changed lines [2, 3, 4, 5, 6, 7, 8, 9, ...]"}, text)

    edit(set())
    for n in range(1, 7):
        (repo / ("note%d.txt" % n)).write_text("x\n", encoding="utf-8")
    six = ["note%d.txt" % n for n in range(1, 7)]
    code, text, result = select()
    check("six changed non-Python files are all named on the \"unseen\" line (no \"...\"), are not gaps, exit code 0",
          code == 0 and text.splitlines()[2:] == ["  unseen (non-Python) 6: " + ", ".join(six)] and result.get("unseen") == six and result.get("gaps") == {}, text)
    (repo / "note7.txt").write_text("x\n", encoding="utf-8")
    code, text, result = select()
    check("seven: the first six and \"...\", the list in the result file still holds all seven",
          code == 0 and text.splitlines()[2:] == ["  unseen (non-Python) 7: " + ", ".join(six) + " ..."] and result.get("unseen") == six + ["note7.txt"], text)
    for n in range(1, 8):
        (repo / ("note%d.txt" % n)).unlink()

    edit({2})
    write_list(repo, [{"name": "t1", "command": "python okay.py"}])
    write_map(repo, {"ghost": {"values.py": [2]}, "t1": {"values.py": [2]}})
    code, text, result = select(run=True)
    check("--run: a selected test that is no longer in the test list (removed or renamed) is left out and said — it is not a failed test (rule changed: it used to be "
          "\"not in list\" with exit code 1, which nothing but a separate commit and a new record could clear)",
          code == 0 and result.get("results") == {"t1": "pass"} and list(result.get("selected", {})) == ["t1"] and list(result.get("seconds", {})) == ["t1"]
          and "left out — in the map but no longer in .oqa/tests.json (removed or renamed): ghost" in text, text)
    write_map(repo, {"t1": {"values.py": [2]}})
    code, text, result = select(run=True)
    check("--run: with every selected test in the list and passing, the exit code is 0", code == 0 and result.get("results") == {"t1": "pass"}, text)
    # an entry that kept its name but runs another command than the one recorded (`init.py --force` gave the name to another file)
    write_list(repo, [{"name": "t1", "command": "python okay.py"}, {"name": "t2", "command": "python okay.py"}])
    write_map(repo, {"t1": {"values.py": [2]}, "t2": {"values.py": [9]}}, commands={"t1": "python okay.py", "t2": "python another.py"})
    code, text, result = select(run=True)
    check("--run: an entry whose command is not the recorded one is run for every change — its recorded lines (line 9, not changed here) belong to the old command",
          code == 0 and result.get("results") == {"t1": "pass", "t2": "pass"} and result.get("selected") == {"t1": ["values.py"], "t2": []}
          and "selected whatever changed — the command is not the one the map was recorded with: t2" in text, (result, text))
    write_map(repo, {"t1": {"values.py": [2]}, "t2": {"values.py": [9]}}, commands={"t1": "python okay.py", "t2": "python okay.py"})
    code, text, result = select(run=True)
    check("… with the recorded command it is left to the map's selection (t2 does not pass through the changed line)", result.get("results") == {"t1": "pass"}, (result, text))
    write_list(repo, [{"name": "t1", "command": "python okay.py"}])
    write_map(repo, {"t1": {"values.py": [2]}})

    # the diff is read whatever prefix the user's git settings ask for
    plain = attempt(impact.changed_old_lines, repo, "working")
    for setting in ("diff.noPrefix", "diff.mnemonicPrefix", "color.ui"):
        git(repo, "config", setting, "always" if setting == "color.ui" else "true")
        got = attempt(impact.changed_old_lines, repo, "working")
        git(repo, "config", "--unset", setting)
        check("changed_old_lines is the same with `%s` set in the repository (the file is still values.py, old line 2 — with the mnemonic prefix it was read as `c/values.py`, a file no test knows)" % setting,
              plain == (({"values.py": {2}}, {}, {}), None) and got == plain, (plain, got))
    asked = []
    real_text = impact.git_text
    impact.git_text = lambda repo_, args: (asked.append(list(args)), real_text(repo_, args))[1]
    try:
        attempt(impact.changed_old_lines, repo, "working")
        attempt(impact.modified_py, repo)
    finally:
        impact.git_text = real_text
    diffs = [args for args in asked if args[0] == "diff"]
    check("both git diff calls of impact.py carry --no-color · --no-ext-diff · --no-textconv · --src-prefix=a/ · --dst-prefix=b/",
          len(diffs) == 2 and all({"--no-color", "--no-ext-diff", "--no-textconv", "--src-prefix=a/", "--dst-prefix=b/"} <= set(args) for args in diffs), diffs)
    other = "diff --git c/values.py w/values.py\nindex 1..2 100644\n--- c/values.py\n+++ w/values.py\n@@ -2 +2 @@\n-v2 = 2\n+v2 = 200\n"
    impact.git_text = lambda repo_, args: other if args[0] == "diff" else real_text(repo_, args)
    try:
        got = attempt(impact.changed_old_lines, repo, "working")
        code, text, result = select()
    finally:
        impact.git_text = real_text
    check("a diff whose file lines carry another prefix than a/ · b/ is not read at all: GitFailed naming the line, and select answers 2 \"cannot judge\" — not a selection for a file called `c/values.py`",
          got[0] is None and str(got[1]).startswith("GitFailed: ") and "--- c/values.py" in str(got[1]) and code == 2 and "cannot judge" in text, (got, code, text))

    # lines added next to a line a test passes through: the wording says when they are settled
    (repo / "values.py").write_text(VALUES.replace("v2 = 2\n", "v2 = 2\nextra = 0\n"), encoding="utf-8")
    write_map(repo, {"t1": {"values.py": [1, 2, 3]}})
    code, text, result = select()
    check("added lines after recording: the gap text starts with \"<n> added line(s)\" and says they are settled later in the same report by the mutants on them — it no longer tells the reader to record again (wrong in the middle of a change)",
          result.get("gaps") == {"values.py": "1 added line(s) — added after the map was recorded, so the map cannot say which tests pass through them; they are settled later in the same report by the mutants on those lines"}
          and result.get("added_ranges") == {"values.py": [[3, 3]]} and "record again" not in text, (result.get("gaps"), text))

    # a deleted .py file has no lines to cover
    edit(set())
    (repo / "other.py").unlink()
    write_map(repo, {"t1": {"values.py": [1, 2, 3]}})
    code, text, result = select()
    check("a deleted .py file no test passed through is not a gap (it used to be reported as \"new file — no test passed through it\") · it is still a changed file · exit code 0",
          code == 0 and result.get("gaps") == {} and result.get("changed") == ["other.py"] and "gap" not in text, (code, result, text))
    write_map(repo, {"t1": {"values.py": [1, 2, 3]}, "t2": {"other.py": [1]}})
    code, text, result = select()
    check("… and the tests that passed through the deleted file are still selected", result.get("selected") == {"t2": ["other.py"]} and result.get("gaps") == {}, (result, text))
    (repo / "fresh.py").write_text("y = 1\n", encoding="utf-8")
    code, text, result = select()
    check("a new .py file no test passed through is still a gap", result.get("gaps") == {"fresh.py": "new file — no test passed through it"}, result.get("gaps"))


def oqa_folder_checks():
    """A repository that does not ignore .oqa/: the spec, the test list, the map and whatever else lies there are not part of the change"""
    repo = new_repo({"values.py": VALUES, ".gitignore": "__pycache__/\n"})
    (repo / "values.py").write_text(VALUES.replace("v2 = 2\n", "v2 = 200\n"), encoding="utf-8")
    write_list(repo, [{"name": "t1", "command": "python values.py"}])
    write_map(repo, {"t1": {"values.py": [1, 2, 3]}})
    (repo / ".oqa" / "specs").mkdir()
    for name, text in ((".oqa/specs/a.md", "# Spec\n"), (".oqa/current", "a.md\n"), (".oqa/hook.js", "x\n"), (".oqa/tool.py", "y = 1\n")):
        (repo / name).write_text(text, encoding="utf-8")
    (repo / "notes.txt").write_text("n\n", encoding="utf-8")
    value, error, text = printed(impact.select, repo, "working", False, PATTERN, SELECT_JSON)
    result = read_json(SELECT_JSON) if error is None else {}
    check("files under .oqa/ are in neither list: changed is the code file and the note, unseen is the note alone, and a .py file under .oqa/ is not a gap"
          " — they used to be listed as unseen and counted as changed files (the report said 5 where the plan said 2)",
          value == 0 and result.get("changed") == ["notes.txt", "values.py"] and result.get("unseen") == ["notes.txt"] and result.get("gaps") == {}
          and "  unseen (non-Python) 1: notes.txt" in text.splitlines() and ".oqa" not in text.split("\n", 1)[1], error or (result, text))


section("select — files under .oqa/ are not part of the change", oqa_folder_checks)


def rename_checks():
    """A renamed file is a deleted file and a new one — as core.py lists it. With git's rename detection the old lines were looked up under the new path"""
    body = "".join("line%d = %d\n" % (n, n) for n in range(1, 21))
    repo = new_repo({"tests/test_old.py": body, "values.py": VALUES, ".gitignore": "__pycache__/\n"})
    git(repo, "mv", "tests/test_old.py", "tests/test_new.py")
    (repo / "tests" / "test_new.py").write_text(body + "line21 = 21\n", encoding="utf-8")       # renamed and edited: git calls it a rename (95% alike)
    write_list(repo, [{"name": "t1", "command": "python tests/test_new.py"}])
    write_map(repo, {"t1": {"tests/test_old.py": list(range(1, 21))}})
    value, error, text = printed(impact.select, repo, "working", False, PATTERN, SELECT_JSON)
    result = read_json(SELECT_JSON) if error is None else {}
    check("a renamed and edited test file comes out as the old path (deleted) and the new path as a new file — not as \"changed lines\" of the new path that no test "
          "passed through, a gap nothing could lift",
          result.get("changed") == ["tests/test_new.py", "tests/test_old.py"] and (result.get("gaps") or {}).get("tests/test_new.py", "").startswith("new file")
          and "tests/test_old.py" not in (result.get("gaps") or {}), error or (result, text))
    check("the diff options every tool shares ask for no rename detection", "--no-renames" in impact.DIFF_OPTIONS, impact.DIFF_OPTIONS)


section("select — a renamed file", rename_checks)


section("select — printed lines, gaps, unseen files, tests missing from the list", select_checks)


# ───────── the Windows job object ─────────
SLEEP = [sys.executable, "-c", "import time; time.sleep(60)"]
# waits for a line, starts a child, says the child's pid, waits for another line and exits — the child stays
PARENT = ("import subprocess, sys\nsys.stdin.readline()\n"
          "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
          "print(child.pid, flush=True)\nsys.stdin.readline()\n")
JOB_FUNCTIONS = ("CreateJobObjectW", "AssignProcessToJobObject", "TerminateJobObject", "CloseHandle")


def exit_code_within(handle, seconds):
    """The exit code of the process behind the handle, or None when it has not ended within the time"""
    if K.WaitForSingleObject(handle, int(seconds * 1000)) != 0:
        return None
    code = ctypes.c_uint(0)
    K.GetExitCodeProcess(handle, ctypes.byref(code))
    return code.value


class Spy:
    """Stands in for one kernel32 function: passes the declarations and the call on, and remembers arguments and result"""

    def __init__(self, real, fail=False):
        self.__dict__.update(real=real, calls=[], fail=fail)

    def __setattr__(self, key, value):
        setattr(self.real, key, value)

    def __call__(self, *args):
        if self.fail:
            raise OSError("refused by the test")
        result = self.real(*args)
        self.calls.append((args, result))
        return result


def job_declaration_checks():
    kernel = ctypes.windll.kernel32
    for name in JOB_FUNCTIONS:                               # earlier checks in this process already made jobs — start from undeclared functions
        getattr(kernel, name).argtypes = None
    kernel.CreateJobObjectW.restype = ctypes.c_int
    proc = subprocess.Popen(SLEEP)
    try:
        job, error = attempt(impact._Job, proc)
        handle = getattr(job, "handle", None)
        check("a job is made for a started process on Windows (a handle, not None)", bool(handle), error or handle)
        check("the job handle comes back at pointer size", kernel.CreateJobObjectW.restype is ctypes.c_void_p, kernel.CreateJobObjectW.restype)
        declared = {name: tuple(getattr(kernel, name).argtypes or ()) for name in JOB_FUNCTIONS}
        check("CreateJobObjectW takes (pointer, wide string)", declared["CreateJobObjectW"] == (ctypes.c_void_p, ctypes.c_wchar_p), declared)
        check("AssignProcessToJobObject takes two pointer-size handles", declared["AssignProcessToJobObject"] == (ctypes.c_void_p, ctypes.c_void_p), declared)
        check("TerminateJobObject takes (pointer-size handle, unsigned exit code)", declared["TerminateJobObject"] == (ctypes.c_void_p, ctypes.c_uint), declared)
        check("CloseHandle takes a pointer-size handle", declared["CloseHandle"] == (ctypes.c_void_p,), declared)
        check("the started process is still running while the job is open", proc.poll() is None, proc.poll())
        _, error = attempt(job.close)
        code, wait_error = attempt(proc.wait, timeout=8)
        check("closing the job ends the process in it, with exit code 1 (so the process really was put into the job)", code == 1, error or wait_error or code)
        check("close forgets the handle", getattr(job, "handle", "missing") is None, getattr(job, "handle", "missing"))
        flags = ctypes.c_uint(0)
        check("... and the handle is closed (the system no longer knows it)", bool(handle) and not K.GetHandleInformation(handle, ctypes.byref(flags)), handle)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def with_leftover_child(body):
    """Start the parent, hand (job-less parent process, function that makes the child and returns a handle on it) to body"""
    parent = subprocess.Popen([sys.executable, "-c", PARENT], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    child_handle, child_pid = None, None
    try:
        job = impact._Job(parent)

        def leave_child():
            nonlocal child_handle, child_pid
            parent.stdin.write(b"go\n")
            parent.stdin.flush()
            child_pid = int(parent.stdout.readline().strip())
            child_handle = K.OpenProcess(0x00101000, 0, child_pid)      # SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION
            parent.stdin.write(b"leave\n")
            parent.stdin.flush()
            parent.wait(timeout=10)
            return child_handle
        body(job, leave_child)
        job.close()
    finally:
        if parent.poll() is None:
            parent.kill()
        if child_pid is not None and child_handle and exit_code_within(child_handle, 0) is None:
            force_end(child_pid)
        if child_handle:
            K.CloseHandle(child_handle)


def job_terminate_checks():
    def body(job, leave_child):
        child = leave_child()
        check("a grandchild whose parent has exited stays alive while nobody ends the job", bool(child) and exit_code_within(child, 0.5) is None)
        _, error = attempt(job.terminate)
        code = exit_code_within(child, 8)
        check("terminate ends that grandchild through the job (the process tree no longer leads to it), with exit code 1", code == 1, error or code)
    with_leftover_child(body)


def job_reap_checks():
    def body(job, leave_child):
        job.reap_when_done()
        child = leave_child()
        code = exit_code_within(child, 8)
        check("reap_when_done ends the leftover grandchild as soon as the started process exits, with exit code 1", bool(child) and code == 1, code)
    with_leftover_child(body)


def job_refused_checks():
    kernel = ctypes.windll.kernel32
    real_create, real_close = kernel.CreateJobObjectW, kernel.CloseHandle

    class NoProcess:                                         # the job can be created but this "process" cannot be put into it
        pid, _handle = 0, 0
    create, close = Spy(real_create), Spy(real_close)
    kernel.CreateJobObjectW, kernel.CloseHandle = create, close
    try:
        job, error = attempt(impact._Job, NoProcess)
    finally:
        kernel.CreateJobObjectW, kernel.CloseHandle = real_create, real_close
    made = [result for _, result in create.calls]
    check("a process that cannot be put into the job leaves no job (handle None)", error is None and getattr(job, "handle", "missing") is None, error or getattr(job, "handle", "missing"))
    check("... and the job that was created for it is closed again (exactly that handle, once)",
          len(made) == 1 and bool(made[0]) and [args for args, _ in close.calls] == [(made[0],)], (made, close.calls))
    kernel.CreateJobObjectW = Spy(real_create, fail=True)
    try:
        job, error = attempt(impact._Job, NoProcess)
    finally:
        kernel.CreateJobObjectW = real_create
    check("an error while creating the job is not passed on, and leaves no job (handle None)", error is None and getattr(job, "handle", "missing") is None, error or getattr(job, "handle", "missing"))


TREE_ONLY = ("import subprocess, sys\nsys.path.insert(0, sys.argv[1])\nimport impact\n"
             "victim = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
             "class Shim:\n    pid, _handle = victim.pid, 0\n"
             "job = impact._Job(Shim)\njob.terminate()\n"
             "try:\n    victim.wait(timeout=10)\n    ended = True\nexcept subprocess.TimeoutExpired:\n    ended = False\n    victim.kill()\n"
             "print('job handle: %s / victim ended: %s' % (job.handle, ended))\n")


def job_tree_checks():
    (WORK / "tree_only.py").write_text(TREE_ONLY, encoding="utf-8")
    done = subprocess.run([sys.executable, str(WORK / "tree_only.py"), str(TOOLS)], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    check("without a job, terminate ends the process tree by itself — and prints nothing of its own (the output is exactly the one line of the script)",
          (done.stdout + done.stderr).strip() == "job handle: None / victim ended: True" and done.returncode == 0, done.stdout + done.stderr)


if WINDOWS:
    section("_Job on Windows — the job is created, declared and closed", job_declaration_checks)
    section("_Job on Windows — terminate reaches a grandchild whose parent has exited", job_terminate_checks)
    section("_Job on Windows — reap_when_done", job_reap_checks)
    section("_Job on Windows — a job that cannot be used", job_refused_checks)
    section("_Job on Windows — terminate without a job", job_tree_checks)
else:
    print("_Job — the Windows job object")
    skip("the job object lines (creating, declaring, assigning, terminating and closing the job, taskkill) run only on Windows")

    def group_checks():
        proc = subprocess.Popen(SLEEP, start_new_session=True)
        try:
            job, error = attempt(impact._Job, proc)
            check("elsewhere there is no job handle, and the unit is the process group of the started process",
                  error is None and job.handle is None and job.group == proc.pid, error)
            attempt(job.close)
            code, wait_error = attempt(proc.wait, timeout=8)
            check("closing ends the process group (the process was killed by a signal)", code is not None and code < 0, wait_error or code)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
    section("_Job elsewhere — the process group", group_checks)

print("failures: %d" % len(failures))
sys.exit(1 if failures else 0)
