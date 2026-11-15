# -*- coding: utf-8 -*-
"""Decisions of `mutate.py` that no other test pinned down — each check names one decision and compares one exact result.

    python tests/mutate_gaps_test.py        (from the repository root)

Order: the functions called directly first (mutant building, test selection, verdicts, texts), then the two git
functions on one small real repository, then whole runs of `mutate.main` on that repository. A call that raises is
turned into a failed check with the error as its text — a check never passes or fails through an unrelated crash.
"""
import _harness  # noqa: F401,E402 — collects the temp folders in one place for this test and removes it at the end (first)
import ast
import contextlib
import io
import itertools
import json
import locale
import os
import pathlib
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
TOOLS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(TOOLS))
import mutate  # noqa: E402

failures = []


def check(name, condition, text=""):
    print(("  ok  " if condition else "  FAIL  ") + name)
    if not condition:
        failures.append(name)
        if text != "":
            print("        " + str(text)[:600].replace("\n", " / "))


def attempt(function, *args, **options):
    """What the call returned — or, when it raised, a text that starts with "raised" (so the comparison after it fails)"""
    try:
        return function(*args, **options)
    except (Exception, SystemExit) as error:              # noqa: BLE001
        return "raised %s: %s" % (type(error).__name__, error)


@contextlib.contextmanager
def patched(owner, name, value):
    """Replace one attribute for the duration of the block and put the old one back"""
    old = getattr(owner, name)
    setattr(owner, name, value)
    try:
        yield
    finally:
        setattr(owner, name, old)


def write(path, text):
    """Write with \\n line ends on every system (line numbers and line texts are compared below)"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), "-c", "user.email=t@t", "-c", "user.name=t"] + list(args), capture_output=True, check=True)


# ───────── mutants(): which nodes become mutants ─────────
print("mutants — only nodes on the asked lines · True and False are swapped · a return of None is left alone")
SAMPLE = ("def f(a):\n    if a > 1:\n        return 5\n    flag = True\n    off = False\n    return None\n\n\n"
          "def g():\n    return\n")


def places(text, lines):
    got = attempt(mutate.mutants, text, lines)
    return got if isinstance(got, str) else sorted((spot["line"], spot["kind"]) for spot in got)


EVERY = [(2, "compare Gt → GtE"), (2, "negate condition"), (2, "number +1"), (3, "number +1"), (3, "return value → None"),
         (4, "True ↔ False"), (4, "statement → pass"), (5, "True ↔ False"), (5, "statement → pass"), (6, "statement → pass"), (10, "statement → pass")]
check("lines = None (the whole file): every mutable node of every line, exactly these eleven", places(SAMPLE, None) == EVERY, places(SAMPLE, None))
check("lines = {2}: only the three mutants of line 2 (the return on line 3, inside the same if, is not taken)",
      places(SAMPLE, {2}) == [(2, "compare Gt → GtE"), (2, "negate condition"), (2, "number +1")], places(SAMPLE, {2}))
check("lines = {3}: the return and its number — not the condition of the if that holds them", places(SAMPLE, {3}) == [(3, "number +1"), (3, "return value → None")], places(SAMPLE, {3}))
check("lines = an empty set (nothing changed): no mutant at all", places(SAMPLE, set()) == [], places(SAMPLE, set()))
SPREAD = "total = (1 +\n         2)\n"
check("a statement spread over two lines when only its second line changed: the statement still overlaps (its mutant is placed at its first line) · the number on line 1 is not taken",
      places(SPREAD, {2}) == [(1, "statement → pass"), (2, "number +1")], places(SPREAD, {2}))
spots = attempt(mutate.mutants, SAMPLE, None)
swaps = {spot["line"]: spot for spot in spots if spot["kind"] == "True ↔ False"} if isinstance(spots, list) else {}
check("True ↔ False: True on line 4 becomes False · False on line 5 becomes True (the value is really inverted)",
      {line: spot["new_text"] for line, spot in swaps.items()} == {4: "False", 5: "True"}, spots)
applied = [attempt(mutate.apply, SAMPLE, swaps[line]) if line in swaps else "no such mutant" for line in (4, 5)]
check("… and applying them changes only that word of that line",
      applied == [SAMPLE.replace("flag = True", "flag = False"), SAMPLE.replace("off = False", "off = True")], applied)
returns = [line for line, kind in (EVERY if isinstance(places(SAMPLE, None), str) else places(SAMPLE, None)) if kind == "return value → None"]
check("return value → None: `return 5` gets the mutant · `return None` (line 6) and a bare `return` (line 10) do not", returns == [3], returns)
check("… a file whose only return is `return None` gets no value mutant — the statement itself is taken out (an early return nobody tests must not pass as \"nothing to mutate\")",
      places("def h():\n    return None\n", None) == [(2, "statement → pass")], places("def h():\n    return None\n", None))
LEAVING = "def k(xs):\n    for x in xs:\n        if x:\n            continue\n        if x is None:\n            break\n        del x\n    raise ValueError('no')\n"
check("raise · continue · del are taken out too (statement → pass) — they used to have no mutant at all. `break` (line 6) is not: without it a loop may never end",
      [line for line, kind in places(LEAVING, None) if kind == "statement → pass"] == [4, 7, 8], places(LEAVING, None))
DECORATED = "import functools\n\n\n@functools.lru_cache\ndef f(x):\n    return x\n\n\nclass C:\n    @property\n    def p(self):\n        return 1\n"
check("a decorator is replaced by one that hands the function back (decorator → identity) — also when only the decorator line is asked about",
      [(line, kind) for line, kind in places(DECORATED, None) if kind == "decorator → identity"] == [(4, "decorator → identity"), (10, "decorator → identity")]
      and places(DECORATED, {4}) == [(4, "decorator → identity")], (places(DECORATED, None), places(DECORATED, {4})))
decorated = attempt(mutate.apply, DECORATED, [s for s in attempt(mutate.mutants, DECORATED, {4})][0])
check("… the mutant is valid Python and the function still exists", isinstance(decorated, str) and "@lambda _same: _same\ndef f(x):" in decorated and compile(decorated, "x", "exec") is not None, decorated)
QUIET = "import os\nfrom a import b\n\n# a comment\n\"\"\"text\"\"\"\n\n\ndef f(x):\n    \"\"\"doc\"\"\"\n    print(x)\n    pass\n    raise ValueError(x)\n"
check("inert_only: imports · comments · blank lines · docstrings · a def header · print · pass hold nothing that changes behaviour",
      mutate.inert_only(QUIET, 1, 11) is True and mutate.inert_only("", 1, 10 ** 9) is True, None)
check("… a `raise` in the range is not nothing — whether or not a mutant can be built on it · and only the lines asked about count",
      mutate.inert_only(QUIET, 12, 12) is False and mutate.inert_only(QUIET, 1, 12) is False and mutate.inert_only(QUIET, 1, 11) is True, None)
check("… a statement with a body counts by its header only: a comment added inside an `if` body is inert, the `if` line itself is not",
      mutate.inert_only("if a:\n    # note\n    b = 1\n", 2, 2) is True and mutate.inert_only("if a:\n    # note\n    b = 1\n", 1, 1) is False
      and mutate.inert_only("if a:\n    # note\n    b = 1\n", 3, 3) is False, None)

# ───────── pick_tests(): which tests, by which method ─────────
print("pick_tests — file not in map · line level · file level without hunk information")
fake_map = {"P": {"f.py": [2, 3]}, "Q": {"f.py": [9]}, "R": {"g.py": [1]}}


def same_line(new):
    return new


def line_five_is_new(new):                                 # no `.hunk` on purpose: a mapper that cannot say which old lines the hunk had
    return None if new == 5 else new


got = attempt(mutate.pick_tests, fake_map, "f.py", None, {"line": 2}, ["D1", "D2"], ["Z"])
check("no old-line mapper (an untracked file) although tests passed through the file: all default tests + tests not in the map, \"file not in map\"",
      got == (["D1", "D2", "Z"], "file not in map"), got)
got = attempt(mutate.pick_tests, fake_map, "h.py", same_line, {"line": 2}, ["D1", "D2"])
check("a mapper exists but no test passed through the file: also \"file not in map\" with the default tests (not line level with nothing)",
      got == (["D1", "D2"], "file not in map"), got)
got = attempt(mutate.pick_tests, fake_map, "f.py", same_line, {"line": 3}, ["D1"])
check("a mapper and a file in the map: line level — only P passed through line 3", got == (["P"], "line level"), got)
got = attempt(mutate.pick_tests, fake_map, "f.py", same_line, {"line": 3, "end_line": 9}, ["D1"], ["Z"])
check("a node from line 3 to 9: P and Q, plus the test not in the map", got == (["P", "Q", "Z"], "line level"), got)
got = attempt(mutate.pick_tests, fake_map, "f.py", line_five_is_new, {"line": 3, "end_line": 5}, ["D1"])
check("a line without an old line and a mapper without hunk information: every test that passed through the file, \"file level\" (nothing is called on the missing hunk)",
      got == (["P", "Q"], "file level"), got)

# ───────── classify · minutes_text · decode ─────────
print("classify · minutes_text · decode")
table = {(failed, all_zero, timed_out): attempt(mutate.classify, failed, all_zero, timed_out) for failed, all_zero, timed_out in itertools.product((True, False), repeat=3)}
check("classify: a failure mark is killed whatever else · no mark and exit code 0 without timeout is survived · no mark with a timeout or a non-zero exit code is crashed",
      table == {(True, True, True): "killed", (True, True, False): "killed", (True, False, True): "killed", (True, False, False): "killed",
                (False, True, False): "survived", (False, True, True): "crashed", (False, False, False): "crashed", (False, False, True): "crashed"}, table)
texts = [attempt(mutate.minutes_text, seconds) for seconds in (89, 90, 5399, 5400)]
check("minutes_text at the borders: 89 → 89s · 90 → 2 min (minutes start at 90) · 5399 → 90 min · 5400 → 1.5 h (hours start at 5400)",
      texts == ["89s", "2 min", "90 min", "1.5 h"], texts)


def encoding_named(name):
    """A stand-in for locale.getpreferredencoding that answers only the question mutate asks (False = do not change the process locale)"""
    return lambda do_setlocale=True: name if do_setlocale is False else "ascii"


with patched(locale, "getpreferredencoding", encoding_named("latin-1")):
    legacy = attempt(mutate.decode, b"caf\xe9 FAILED")
with patched(locale, "getpreferredencoding", encoding_named("")):
    unnamed = attempt(mutate.decode, b"caf\xe9 FAILED")
check("decode: bytes that are not UTF-8 are read in the system's legacy encoding (asked for without changing the process locale)", legacy == "café FAILED", ascii(legacy))
check("decode: when the system names no encoding, UTF-8 with replacement characters (text, not None)", unnamed == "caf� FAILED", ascii(unnamed))
check("decode: valid UTF-8 is read as UTF-8", attempt(mutate.decode, "café".encode("utf-8")) == "café")

# ───────── _unlock_and_remove ─────────
print("_unlock_and_remove — unlock, then delete again")
locked_folder = Path(tempfile.mkdtemp())
locked_file = locked_folder / "read_only.txt"
write(locked_file, "x\n")
os.chmod(locked_file, stat.S_IREAD)
modes, real_chmod = [], os.chmod


def watching_chmod(path, mode, *rest, **options):
    modes.append((str(path), mode))
    return real_chmod(path, mode, *rest, **options)


with patched(os, "chmod", watching_chmod):
    answer = attempt(mutate._unlock_and_remove, os.remove, str(locked_file), None)
check("a read-only file is deleted (it is unlocked first, then the failed delete is repeated)", answer is None and not locked_file.exists(), (answer, locked_file.exists()))
check("… it is unlocked for its owner only: mode 0o700, nothing for group or others", modes == [(str(locked_file), 0o700)], modes)


def cannot(path):
    raise PermissionError(13, "Permission denied", path)


write(locked_file, "x\n")
check("when the second delete fails too, it moves on without raising (the next run is not blocked)", attempt(mutate._unlock_and_remove, cannot, str(locked_file), None) is None)

# ───────── run_tests: its own temp folder ─────────
print("run_tests — the temp folder of a call is removed, a second time when the first try leaves it")
real_rmtree, real_sleep = shutil.rmtree, time.sleep


def watch_cleanup(first_try_works):
    """(result of run_tests without commands, temp folders handed to rmtree, sleeps asked for)"""
    removed, sleeps = [], []

    def rmtree(path, *rest, **options):
        if "oqa_mutate_tmp_" not in str(path):
            return real_rmtree(path, *rest, **options)
        removed.append(str(path))
        if len(removed) == 1 and not first_try_works:
            return None                                      # as if a process that was just ended still held a file: nothing is removed
        return real_rmtree(path, *rest, **options)

    with patched(shutil, "rmtree", rmtree), patched(time, "sleep", lambda seconds: sleeps.append(seconds)):
        result = attempt(mutate.run_tests, [], ".", mutate.DEFAULT_FAIL_PATTERN, 10)
    return result, removed, sleeps


result, removed, sleeps = watch_cleanup(True)
check("no command: no failure mark, every exit code 0, no timeout, empty tail", result == (False, True, False, ""), result)
check("the temp folder is gone after the first try: one removal, no waiting", len(removed) == 1 and sleeps == [] and not os.path.isdir(removed[0]), (removed, sleeps))
result, removed, sleeps = watch_cleanup(False)
check("the first try leaves the folder: it waits half a second and removes the same folder once more — it is gone afterwards",
      len(removed) == 2 and removed[0] == removed[1] and sleeps == [0.5] and not os.path.isdir(removed[0]), (removed, sleeps))
for left in set(removed):
    real_rmtree(left, ignore_errors=True)

# ───────── the fixture repository ─────────
OLD_ROWS = ["v%02d = %d" % (n, n) for n in range(1, 13)]
NEW_ROWS = [("v01 = 1", 1), ("v03 = 3", 3), ("w4a = 40", None), ("w4b = 41", None), ("v05 = 5", 5), ("v06 = 6", 6), ("v07 = 7", 7),
            ("added = 70", None), ("v08 = 8", 8), ("v09 = 9", 9), ("v12 = 12", 12)]      # (text, its old line | None): old 2 removed · old 4 became two lines · one line added after old 7 · old 10 and 11 removed
CALC = "def double(x):\n    return x * 2\n\n\ndef unused(x):\n    return x"       # no line end after the last line: the last mutant sits on the last row
T_CASES = ("import sys\n"
           "picked = sys.argv[sys.argv.index('--case') + 1].split(',') if '--case' in sys.argv else ['a', 'b', 'c', 'd']\n"
           "# case: a\nif 'a' in picked:\n    print('  ok  a')\n"
           "# case: b\nif 'b' in picked:\n    import fresh\n    print('  ok  b' if fresh.triple(2) == 6 else '  FAIL  b')\n"
           "# case: c\nif 'c' in picked:\n    print('  ok  c')\n"
           "# case: d\nif 'd' in picked:\n    import fresh as again\n    print('  ok  d' if again.triple(1) == 3 else '  FAIL  d')\n")
BAD = "def broken(:\n    pass\n"
repo = Path(tempfile.mkdtemp()) / "gaps_repo"
repo.mkdir()
git(repo, "init", "-q")
git(repo, "config", "core.autocrlf", "false")
for name, text in {
    "a.py": "\n".join(OLD_ROWS) + "\n",
    "b.py": "kept = 1\n",
    "c.py": "t1 = 1\nt2 = 2\nt3 = 3\nt4 = 4\nt5 = 5\nt6 = 6\n",
    "calc.py": CALC,
    "spare.py": "def spare(x):\n    return x\n",
    "fresh.py": "def triple(x):\n    return x * 3\n",
    "empty.py": "import os\n",
    "bad.py": BAD,
    "t_double.py": "import calc\nprint('  ok  double' if calc.double(3) == 6 else '  FAIL  double')\n",
    "t_bad.py": "import sys\nprint(('  FAIL  always ' + ' '.join(sys.argv[1:])).rstrip())\nsys.exit(1)\n",
    "t_seen.py": "import fresh\nprint('  ok  triple' if fresh.triple(2) == 6 else '  FAIL  triple')\n",
    "t_cases.py": T_CASES,
    "t_exit3.py": "import sys\nprint('boom line')\nsys.exit(3)\n",
    "t_silent3.py": "import sys\nsys.exit(3)\n",
    "t_fail_then.py": "import sys\nprint('  FAIL  first')\nprint('bye')\nsys.exit(1)\n",
}.items():
    write(repo / name, text)
git(repo, "add", "-A")
git(repo, "commit", "-qm", "first")
write(repo / "a.py", "\n".join(text for text, _ in NEW_ROWS) + "\n")
write(repo / "c.py", "t3 = 3\nt4 = 4\n")                    # the first two and the last two lines removed
write(repo / "new.py", "fresh_value = 1\n")                # not tracked

# ───────── run_tests: the tail of a run that ends with an error ─────────
print("run_tests — what the tail says when a test ends with an error")
got = attempt(mutate.run_tests, ["python t_exit3.py"], repo, mutate.DEFAULT_FAIL_PATTERN, 60, stop_on_kill=True)
check("mutant path, exit code 3 without a failure line: killed, and the tail ends with the last output line",
      got == (True, False, False, "ended with an error (exit code 3) — boom line"), got)
got = attempt(mutate.run_tests, ["python t_silent3.py"], repo, mutate.DEFAULT_FAIL_PATTERN, 60, stop_on_kill=True)
check("mutant path, exit code 3 and no output at all: killed, with an empty last line (no error from the missing line)",
      got == (True, False, False, "ended with an error (exit code 3) — "), got)
got = attempt(mutate.run_tests, ["python t_exit3.py"], repo, mutate.DEFAULT_FAIL_PATTERN, 60)
check("baseline path, exit code 3 without a failure line: no mark, not all zero, and the tail is the last output line", got == (False, False, False, "boom line"), got)
got = attempt(mutate.run_tests, ["python t_fail_then.py"], repo, mutate.DEFAULT_FAIL_PATTERN, 60)
check("baseline path, a failure line and then more output with exit code 1: the tail stays the failure line (not the last line)", got == (True, False, False, "FAIL  first"), got)

# ───────── changed_lines ─────────
print("changed_lines — the new-side lines of the hunks · an untracked file is changed as a whole")
check("range all: None (every line), without asking git", attempt(mutate.changed_lines, repo, "a.py", "all") is None)
got = attempt(mutate.changed_lines, repo, "a.py", "working")
check("a modified tracked file: exactly the added and changed lines (3 · 4 · 8) — removed lines add nothing", got == {3, 4, 8}, got)
got = attempt(mutate.changed_lines, repo, "c.py", "working")
check("a tracked file that only lost lines: the empty set (a diff exists, so it is not taken as a new file)", got == set(), got)
got = attempt(mutate.changed_lines, repo, "b.py", "working")
check("an unchanged tracked file: the empty set (not None — nothing is changed)", got == set(), got)
got = attempt(mutate.changed_lines, repo, "new.py", "working")
check("an untracked file (empty diff, unknown to git): None — the whole file counts as changed", got is None, got)
LONG = "".join(chr(ord("a") + n % 26) for n in range(400))
real_run = subprocess.run


def failing_git(args, *rest, **options):
    """git as it would answer when the diff fails with a long message (ls-files still answers "tracked")"""
    if "ls-files" in args:
        return subprocess.CompletedProcess(args, 0, b"", b"")
    return subprocess.CompletedProcess(args, 1, "", "  " + LONG + "\n")


with patched(subprocess, "run", failing_git):
    stopped = attempt(mutate.changed_lines, repo, "a.py", "working")
    stopped_mapper = attempt(mutate.old_line_mapper, repo, "a.py")
check("changed_lines when git diff fails: it stops with the last 300 characters of git's message", stopped == "raised SystemExit: git diff failed: " + LONG[-300:], stopped)
check("old_line_mapper when git diff fails: the same stop with the last 300 characters", stopped_mapper == "raised SystemExit: git diff failed: " + LONG[-300:], stopped_mapper)
stopped = attempt(mutate.changed_lines, repo, "a.py", "no_such_ref_xyz")
check("a range git does not know: it stops and names the failed diff (not an empty set)", isinstance(stopped, str) and stopped.startswith("raised SystemExit: git diff failed: ") and "no_such_ref_xyz" in stopped, stopped)

# the diff is read whatever the user's git settings say
print("changed_lines · old_line_mapper — the user's git settings do not change what is read")
diff_calls = []


def spying_git(args, *rest, **options):
    if "diff" in args:
        diff_calls.append(list(args))
    return real_run(args, *rest, **options)


git(repo, "config", "color.ui", "always")                  # colours every diff, also when the output is not a screen: the hunk lines then start with an escape code
git(repo, "config", "diff.mnemonicPrefix", "true")
with patched(subprocess, "run", spying_git):
    coloured = attempt(mutate.changed_lines, repo, "a.py", "working")
    coloured_mapper = attempt(mutate.old_line_mapper, repo, "a.py")
    coloured_ranges = attempt(lambda: mutate.added_ranges(repo, "a.py", "working"))
coloured_old = [attempt(coloured_mapper, n) for n in range(1, len(NEW_ROWS) + 1)] if callable(coloured_mapper) else coloured_mapper
git(repo, "config", "--unset", "color.ui")
git(repo, "config", "--unset", "diff.mnemonicPrefix")
check("with `color.ui always` set in the repository the changed lines are still 3 · 4 · 8 and every line still maps to its old line — the coloured `@@` lines were not recognised, so the file read as unchanged and no mutant was built",
      coloured == {3, 4, 8} and coloured_old == [old for _, old in NEW_ROWS], (coloured, coloured_old))
WANTED = {"--no-color", "--no-ext-diff", "--no-textconv", "--src-prefix=a/", "--dst-prefix=b/"}
check("every git diff call of mutate.py carries --no-color · --no-ext-diff · --no-textconv · --src-prefix=a/ · --dst-prefix=b/ (changed lines · old-line mapper · added ranges)",
      len(diff_calls) == 3 and all(WANTED <= set(args) for args in diff_calls), diff_calls)
check("added_ranges: the new-side line range of each hunk that only adds lines (line 8) — a changed hunk (3 · 4) is not one · range all and an untracked file give none",
      coloured_ranges == [[8, 8]] and attempt(lambda: mutate.added_ranges(repo, "a.py", "all")) == [] and attempt(lambda: mutate.added_ranges(repo, "new.py", "working")) == [], coloured_ranges)


def unreadable_diff(args, *rest, **options):
    """git as it would answer through a diff program this tool did not ask for: exit code 0, but no `diff --git` line and no plain `@@` line"""
    if "ls-files" in args:
        return subprocess.CompletedProcess(args, 0, b"", b"")
    return subprocess.CompletedProcess(args, 0, "\x1b[1mdiff --git a/a.py b/a.py\x1b[m\n\x1b[36m@@ -1 +1 @@\x1b[m\n", "")


with patched(subprocess, "run", unreadable_diff):
    stopped = attempt(mutate.changed_lines, repo, "a.py", "working")
    stopped_mapper = attempt(mutate.old_line_mapper, repo, "a.py")
check("a diff output that cannot be recognised (it does not start with `diff --git`) stops both functions — it is unknown, not \"no changed lines\"",
      str(stopped).startswith("raised SystemExit: git diff output could not be read") and str(stopped_mapper).startswith("raised SystemExit: git diff output could not be read"), (stopped, stopped_mapper))

# ───────── old_line_mapper ─────────
print("old_line_mapper — every new line of a file with removed, changed and added hunks")
mapper = attempt(mutate.old_line_mapper, repo, "a.py")
numbers = range(1, len(NEW_ROWS) + 1)
got = [attempt(mapper, n) for n in numbers] if callable(mapper) else mapper
check("each line that was kept maps to its old line (1→1 · 2→3 · 5→5 … 11→12) and each added or changed line to None",
      got == [old for _, old in NEW_ROWS], got)
got = {n: attempt(mapper.hunk, n) for n in numbers} if callable(mapper) else mapper
check("hunk: the two lines of the changed hunk give its old line {4} · the added line gives the old lines around it {7, 8} · every other line — also the one right after a hunk — the empty set",
      got == {n: ({4} if n in (3, 4) else {7, 8} if n == 8 else set()) for n in numbers}, got)
mapper = attempt(mutate.old_line_mapper, repo, "c.py")
got = [attempt(mapper, n) for n in (1, 2)] if callable(mapper) else mapper
check("lines removed at the very top and at the end: the two lines left map to old 3 and 4", got == [3, 4], got)
check("an untracked file has no mapper (None)", attempt(mutate.old_line_mapper, repo, "new.py") is None)

# ───────── whole runs ─────────
counter = itertools.count()


def run_main(*args):
    """(what main returned | "raised …", everything it printed, the --json result | {})"""
    result_path = repo.parent / ("result_%d.json" % next(counter))
    printed = io.StringIO()
    with contextlib.redirect_stdout(printed), contextlib.redirect_stderr(printed):
        code = attempt(mutate.main, ["--repo", str(repo), "--json", str(result_path), "--timeout", "60"] + [str(a) for a in args])
    try:
        data = json.loads(result_path.read_text(encoding="utf-8")) if result_path.is_file() else {}
    except ValueError as error:
        data = {"unreadable": str(error)}
    return code, printed.getvalue() + (code if isinstance(code, str) else ""), data


def table_file(name, rows):
    path = repo.parent / name
    write(path, json.dumps(rows))
    return path


DOUBLE = "python t_double.py"
print("main — the printed rows, the mutated line, and --max equal to the number of mutants")
code, out, data = run_main("--files", "calc.py", "--range", "all", "--test", DOUBLE, "--max", "3")
rows = [(r["line"], r["kind"], r["verdict"], r["mutated_line"]) for r in data.get("mutants", [])]
check("three mutants, in the order they were built: two killed without a mutated line, the survivor (on the last row of the file) with its mutated line",
      rows == [(2, "return value → None", "killed", None), (6, "return value → None", "survived", "return None"), (2, "number +1", "killed", None)], (rows, out))
check("exit code 1 (one survived) · counts · no map given: line_level is false",
      code == 1 and data.get("counts") == {"killed": 2, "survived": 1, "crashed": 0, "gap": 0} and data.get("line_level") is False, (code, data.get("counts"), data.get("line_level")))
check("--max equal to the number of mutants: all run and nothing is said about picking", "picked evenly" not in out and data.get("generated") == 3, out)
check("the row of a killed mutant ends after the selection — no tail and no mutated line", "killed   calc.py:2  return value → None  [all 1]  \n" in out, out)
check("the row of a survivor shows its mutated line", "survived calc.py:6  return value → None  [all 1]    | mutated line: return None\n" in out, out)
code, out, data = run_main("--files", "calc.py", "--range", "all", "--test", DOUBLE, "--max", "2")
rows = [(r["line"], r["kind"]) for r in data.get("mutants", [])]
check("--max below the number of mutants: it says so and runs the first and the last",
      "3 mutants, running only 2 picked evenly (--max)" in out and rows == [(2, "return value → None"), (2, "number +1")] and data.get("generated") == 3, (rows, out))

print("main — nothing to mutate · files that cannot be mutated")
code, out, data = run_main("--files", "empty.py", "--range", "all", "--test", DOUBLE)
check("no node to mutate: main returns 0 (the number, not nothing) · baseline not run · generated 0",
      code is not None and code == 0 and "0 mutants generated" in out and data.get("generated") == 0 and data.get("baseline") == "not run", (code, out, data))
try:
    (repo / "ghost_target.py").read_text(encoding="utf-8")
    missing_reason = None
except OSError as error:
    missing_reason = error.strerror
code, out, data = run_main("--files", "ghost_target.py", "--range", "all", "--test", DOUBLE)
check("a target file that does not exist: exit code 2 · the basis gives the system's reason · nothing is counted",
      missing_reason and code == 2 and data == {"baseline": "not run", "generated": None, "basis": "ghost_target.py cannot be read (%s)" % missing_reason, "mutants": [], "test_runs": 0, "seconds": 0}
      and "cannot mutate — ghost_target.py cannot be read (%s)." % missing_reason in out, (code, out, data))
real_read_text = pathlib.Path.read_text


def unreadable(file_name, error):
    def read_text(self, *rest, **options):
        if self.name == file_name:
            raise error
        return real_read_text(self, *rest, **options)
    return read_text


with patched(pathlib.Path, "read_text", unreadable("spare.py", OSError())):
    code, out, data = run_main("--files", "spare.py", "--range", "all", "--test", DOUBLE)
check("a read error that carries no reason text: the basis names the kind of error instead", code == 2 and data.get("basis") == "spare.py cannot be read (OSError)", (code, out, data))
try:
    ast.parse(BAD)
    expected_syntax = None
except SyntaxError as error:
    expected_syntax = "bad.py does not parse as Python (%s, line %s)" % (error.msg, error.lineno)
code, out, data = run_main("--files", "bad.py", "--range", "all", "--test", DOUBLE)
check("a syntax error: exit code 2 · the basis is the parser's short message and the line (not the whole error text)",
      expected_syntax and code == 2 and data.get("basis") == expected_syntax and data.get("generated") is None, (code, expected_syntax, data))


def null_byte(text, lines):
    raise ValueError("source code string cannot contain null bytes")


with patched(mutate, "mutants", null_byte):
    code, out, data = run_main("--files", "spare.py", "--range", "all", "--test", DOUBLE)
check("an error without a short message (ValueError of older Pythons): the basis is the error text itself",
      code == 2 and data.get("basis") == "spare.py does not parse as Python (source code string cannot contain null bytes)", (code, out, data))

print("main — a failing baseline: which test is marked")
code, out, data = run_main("--files", "calc.py", "--range", "all", "--test", "python t_bad.py")
check("one failing test: exit code 2 · that test is marked \"fail\" in the baseline results · no mutant counted",
      code == 2 and data.get("baseline") == "fail" and data.get("baseline_results") == {"python t_bad.py": "fail"} and data.get("basis") == "python t_bad.py: FAIL  always"
      and data.get("mutants") == [], (code, out, data))
case_rows = [{"name": "bad:%s" % case, "command": "python t_bad.py --case %s" % case, "case_base": "python t_bad.py", "case_name": case} for case in ("one", "two")]
code, out, data = run_main("--files", "calc.py", "--range", "all", "--tests-file", table_file("bad_cases.json", case_rows),
                           "--test", "python t_bad.py --case one", "--test", "python t_bad.py --case two")
check("two cases run as one failing command: exit code 2 · neither case is marked (it is not known which one failed) · the basis names both and the grouped command's line",
      code == 2 and data.get("baseline") == "fail" and data.get("baseline_results") == {} and data.get("basis") == "bad:one, bad:two: FAIL  always --case one,two", (code, out, data))
half_case = [{"name": "T", "command": DOUBLE, "case_base": "python t_bad.py", "case_name": ""}]
code, out, data = run_main("--files", "calc.py", "--range", "all", "--max", "1", "--tests-file", table_file("half_case.json", half_case), "--test", DOUBLE)
check("an entry with a case base but an empty case name is not a case test: its own command runs (baseline passes, the mutant is killed)",
      code == 0 and data.get("baseline") == "pass" and data.get("baseline_results") == {"T": "pass"} and [r["verdict"] for r in data.get("mutants", [])] == ["killed"], (code, out, data))

print("main — only gaps: the estimate is zero, not unknown")
gap_map = table_file("gap_map.json", {"tests": {"A": {"calc.py": [1, 2], "spare.py": [1]}}})
only_a = table_file("only_a.json", [{"name": "A", "command": DOUBLE}])
code, out, data = run_main("--files", "spare.py", "--range", "all", "--map", gap_map, "--tests-file", only_a, "--test", DOUBLE)
rows = [(r["line"], r["verdict"], r["tests"], r["selection"], r["mutated_line"]) for r in data.get("mutants", [])]
check("a line no test passed through and no test outside the map: one gap at line level · exit code 1 · a map was given: line_level is true",
      code == 1 and rows == [(2, "gap", [], "line level", "return None")] and data.get("line_level") is True and data.get("test_runs") == 0, (code, rows, out))
check("no test will run and none lacks a time: the estimate is printed as 0s (it is not \"unknown\") · the line says what the estimate counts — measured test time only",
      "estimate for 1 mutants with 1 job(s): about 0s when every mutant is killed by its first test, up to 0s when none is killed (test time only — copying and start-up come on top)\n" in out
      and "run time is unknown" not in out, out)
check("the row of a gap gives the reason and the mutated line",
      "gap      spare.py:2  return value → None  [line level 0]  no test passed through this mutant's location (line level)  | mutated line: return None\n" in out, out)

print("main — --stop-over at the border and with tests that have no measured time")
timed = table_file("timed.json", [{"name": "A", "command": DOUBLE, "seconds": 60.0}])
code, out, data = run_main("--files", "calc.py", "--range", "all", "--max", "1", "--tests-file", timed, "--test", DOUBLE, "--stop-over", "1")
check("the low estimate equals the limit exactly (60 s against 1 min): not over it — the mutant runs",
      code == 0 and "stopped" not in data and [r["verdict"] for r in data.get("mutants", [])] == ["killed"] and "stopped before" not in out, (code, out, data))
code, out, data = run_main("--files", "calc.py", "--range", "all", "--max", "1", "--tests-file", timed, "--test", DOUBLE, "--stop-over", "0.5")
check("the low estimate over the limit (60 s against 0.5 min): exit code 4 · no mutant ran · the estimate is kept as the reason",
      code == 4 and data.get("mutants") == [] and str(data.get("stopped", "")).startswith("estimate for 1 mutants with 1 job(s): about 60s")
      and "stopped before running the mutants — the low estimate is over 0.5 min." in out, (code, out, data))


# A test without a measured time is timed when it runs unmutated in the copy, so the estimate can always be made in a real run. The stop
# "the run time cannot be estimated" was reachable only through a stand-in for estimate(); it is gone, and so are the checks that reached it that way
untimed = table_file("untimed.json", [{"name": "A", "command": DOUBLE}])
code, out, data = run_main("--files", "calc.py", "--range", "all", "--max", "1", "--tests-file", untimed, "--test", DOUBLE, "--stop-over", "100000")
check("a listed test without a measured time: the time measured in the copy is used — an estimate is printed, no test is said to be left out, and the run goes ahead under the limit",
      code == 0 and "estimate for 1 mutants with 1 job(s): about " in out and "no measured time" not in out and "cannot be estimated" not in out and "run time is unknown" not in out
      and "stopped" not in data and [r["verdict"] for r in data.get("mutants", [])] == ["killed"], (code, out, data))
code, out, data = run_main("--files", "calc.py", "--range", "all", "--max", "1", "--tests-file", untimed, "--test", DOUBLE, "--stop-over", "0.0000001")
check("… and a limit below that measured time stops on the estimate (exit code 4), with the estimate as the reason",
      code == 4 and str(data.get("stopped", "")).startswith("estimate for 1 mutants with 1 job(s): about ") and data.get("mutants") == [], (code, out, data))
code, out, data = run_main("--help")
check("the docstring and the --stop-over help promise the stop on the estimate only (no \"cannot be estimated\" · no \"no measured time\")",
      "--stop-over" in out and not [word for word in ("cannot be estimated", "cannot be checked", "no measured time") if word in " ".join(out.split()) or word in " ".join(mutate.__doc__.split())], out[-900:])

print("main — a file not in the map: tests are picked by the module name in their text")
seen_map = table_file("seen_map.json", {"tests": {"seen": {"t_seen.py": [1]}, "A": {"calc.py": [1, 2]}}})
seen_rows = [{"name": "seen", "command": "GHOST=ghost.py python t_seen.py"}, {"name": "A", "command": DOUBLE}]
seen_table = table_file("seen_table.json", seen_rows)
code, out, data = run_main("--files", "fresh.py", "--range", "all", "--map", seen_map, "--tests-file", seen_table, "--test", DOUBLE)
rows = [(r["selection"], r["tests"], r["verdict"]) for r in data.get("mutants", [])]
check("the test file is the first word of the command that ends in .py and is a file (a leading NAME=value.py word is not): the test that mentions the module is picked, name level, and kills",
      code == 0 and rows == [("name level", ["seen"], "killed")] * 2, (code, rows, out))
with patched(pathlib.Path, "read_text", unreadable("t_seen.py", PermissionError(13, "Permission denied"))):
    code, out, data = run_main("--files", "fresh.py", "--range", "all", "--max", "1", "--map", seen_map, "--tests-file", seen_table, "--test", DOUBLE)
rows = [(r["selection"], r["tests"]) for r in data.get("mutants", [])]
check("a test file that cannot be read mentions nothing: no test is picked by name, so all --test commands run (\"file not in map\")",
      code == 1 and rows == [("file not in map", ["A"])], (code, rows, out))
case_table = table_file("case_table.json", [{"name": "cases:%s" % case, "command": "python t_cases.py --case %s" % case, "case_base": "python t_cases.py", "case_name": case} for case in "abcd"])
case_map = table_file("case_map.json", {"tests": {"cases:%s" % case: {"t_cases.py": [1, 2]} for case in "abcd"}})
code, out, data = run_main("--files", "fresh.py", "--range", "all", "--map", case_map, "--tests-file", case_table, "--test", "python t_cases.py")
rows = [(r["selection"], r["tests"], r["verdict"]) for r in data.get("mutants", [])]
check("case tests: only the text of each case's own block counts — cases b and d mention the module, a and c (and the shared head) do not",
      code == 0 and rows == [("name level", ["cases:b", "cases:d"], "killed")] * 2, (code, rows, out))

print("main — added hunks in which nothing can be mutated are named in the result")
quiet_repo = Path(tempfile.mkdtemp()) / "quiet_repo"
quiet_repo.mkdir()
git(quiet_repo, "init", "-q")
git(quiet_repo, "config", "core.autocrlf", "false")
write(quiet_repo / "calc.py", "def double(x):\n    return x * 2\n")
write(quiet_repo / "t_double.py", "import calc\nprint('  ok  double' if calc.double(3) == 6 else '  FAIL  double')\n")
git(quiet_repo, "add", "-A")
git(quiet_repo, "commit", "-qm", "first")


def quiet_main(*args):
    result_path = quiet_repo.parent / ("quiet_%d.json" % next(counter))
    printed = io.StringIO()
    with contextlib.redirect_stdout(printed), contextlib.redirect_stderr(printed):
        code = attempt(mutate.main, ["--repo", str(quiet_repo), "--files", "calc.py", "--test", DOUBLE, "--json", str(result_path), "--timeout", "60"] + [str(a) for a in args])
    return code, printed.getvalue(), (json.loads(result_path.read_text(encoding="utf-8")) if result_path.is_file() else {})


# lines 1-3 (an import, a comment, a blank line) and lines 6-9 (a new function) are added in two hunks; line 5 is not touched
write(quiet_repo / "calc.py", "import os\n# a note\n\ndef double(x):\n    return x * 2\n\n\ndef half(x):\n    return x / 2\n")
check("(setup) the two added hunks are lines 1-3 and 6-9", attempt(lambda: mutate.added_ranges(quiet_repo, "calc.py", "working")) == [[1, 3], [6, 9]], attempt(lambda: mutate.added_ranges(quiet_repo, "calc.py", "working")))
code, out, data = quiet_main()
check("the result names, per file, the added hunks without any candidate mutant (`import os` · a comment · a blank line: lines 1-3) — the hunk with the new function (6-9) is not one of them",
      data.get("added_without_mutants") == {"calc.py": [[1, 3]]} and bool(data.get("mutants")) and {r["line"] for r in data.get("mutants", [])} <= {8, 9}, (code, data.get("added_without_mutants"), out))
code, out, data = quiet_main("--max", "1")
check("… the statement is about candidates, not about what ran: with --max 1 it is the same", data.get("added_without_mutants") == {"calc.py": [[1, 3]]} and len(data.get("mutants", [])) == 1, (code, data))
write(quiet_repo / "calc.py", "import os\ndef double(x):\n    return x * 2\n")
code, out, data = quiet_main()
check("a change that only adds `import os`: 0 mutants generated, and the result still names that added hunk (line 1) as holding nothing to mutate",
      code == 0 and data.get("generated") == 0 and data.get("added_without_mutants") == {"calc.py": [[1, 1]]}, (code, data, out))
check("… and the file is named in `inert_files`: its change only adds lines and every added hunk is inert (rule changed — the report said \"confirmed\" for the "
      "lines and \"generated 0 — unknown\" for the file)", data.get("inert_files") == ["calc.py"], data.get("inert_files"))
write(quiet_repo / "calc.py", "import os\ndef double(x):\n    return x * 2\n\n\ndef half(x):\n    return x / 2\n")
code, out, data = quiet_main("--test", "python -c \"print('  ok  nothing')\"")
check("… not when another added hunk holds code: the file has mutants and is not inert", data.get("inert_files") == [] and (data.get("generated") or 0) > 0, (data.get("inert_files"), data.get("generated"), out))
write(quiet_repo / "calc.py", "import os\ndef double(x):\n    return x * 2\n\n\ndef guard(x):\n    if x:\n        return 1\n    raise ValueError(x)\n")
code, out, data = quiet_main()
check("an added `raise` is a candidate mutant now, so its hunk is not named as holding nothing to mutate",
      data.get("added_without_mutants") == {"calc.py": [[1, 1]]} and 9 in {r["line"] for r in data.get("mutants", [])}, (code, data.get("added_without_mutants"), out))
write(quiet_repo / "calc.py", "import functools\n\n\n@functools.cache\ndef double(x):\n    pass\n")
code, out, data = quiet_main()
check("a file of bare definitions is not inert when one of them carries a decorator: the decorator has a mutant, so there is something to check",
      data.get("inert_files") == [] and data.get("generated_by_file") == {"calc.py": 1}, (data.get("inert_files"), data.get("generated_by_file"), out))
write(quiet_repo / "calc.py", "\"\"\"Only a docstring and an import.\"\"\"\nimport os\n")
code, out, data = quiet_main()
check("a file that is inert as a whole (a docstring and an import) is named in `inert_files` · 0 mutants generated",
      code == 0 and data.get("generated") == 0 and data.get("inert_files") == ["calc.py"], (code, data, out))
(quiet_repo / "calc.py").write_bytes(b"\xef\xbb\xbfdef double(x):\n    return x * 2 + 0\n")
code, out, data = quiet_main()
check("a source file that starts with a UTF-8 byte order mark is mutated like any other (it used to be \"cannot mutate … U+FEFF\" for every change in it)",
      "cannot mutate" not in out and (data.get("generated") or 0) > 0 and data.get("baseline") == "pass" and {r["line"] for r in data.get("mutants", [])} == {2}, (code, data, out))
write(quiet_repo / "calc.py", "import os\ndef double(x):\n    return x * 2\n")
code, out, data = quiet_main("--range", "all")
check("range all has no added hunks: an empty list for the file", data.get("added_without_mutants") == {"calc.py": []} and len(data.get("mutants", [])) > 0, (code, data))

print("\nfailures: %d" % len(failures))
sys.exit(1 if failures else 0)
