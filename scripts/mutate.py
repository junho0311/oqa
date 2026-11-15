# -*- coding: utf-8 -*-
"""**Break changed lines on purpose and count whether the tests bite.**

    python mutate.py --repo <root> --files a.py [b.py ...] --test "<command>" [--test ...] [--range working|all|A..B] [--max 60] [--json result.json]

Why: hand-written mutation scripts get rewritten again and again, and each rewrite can count differently — for example
a test that hangs must not count as "killed". So generating, running and counting are fixed in one tool.

What it does
1. Picks **only the changed lines** of the target files (git diff — default "working" = between HEAD and now). `--range all` is the whole file
2. Builds the mutants of those lines with ast — flip comparisons (> ↔ >= · < ↔ <= · == ↔ != · in ↔ not in · is ↔ is not), and ↔ or, remove not,
   negate if / while conditions, swap True / False, number +1, return value to None, one statement to pass. **Only the text of that node is replaced** —
   the rest of the file (comments, layout) stays as it is
3. Copies the repository once, and for each mutant changes only that file, runs the test commands and restores it (the original repository is not touched)
4. Sorts into three — **killed**: the test output has a failure mark (fail pattern), or it ended with an error without a failure mark (exit code ≠ 0 —
   the original passed, so the mutant is to blame) / **survived**: no failure mark and exit code 0 / **crashed**: timeout (the test hung, so
   it is unknown whether the test would have caught the mutant)
5. Before any mutant runs, every test the mutants will use is run once on the unmutated copy (baseline); if one already has a failure mark, a non-zero
   exit code or a timeout it stops — counting on a broken baseline makes everything "killed". The copy leaves out `.git`, `.oqa`, `node_modules`, …,
   so a test can pass in the repository and fail in the copy: that is a baseline failure too, and the basis says so
6. A target file that cannot be read as Python (not valid UTF-8, a syntax error) stops the run before anything is counted: exit code 2, baseline "not run",
   and the basis names the file and the problem

Keeping it light (running **all** selected tests for every mutant takes hours):
- With `--map impact-map.json --tests-file tests.json` **only the tests that passed through that line** run for each mutant. The map holds old (HEAD side)
  line numbers, so the current file (new lines) is matched to old lines through the `git diff -U0 HEAD` hunks (a line outside a hunk is shifted by the
  line-count difference of the hunks before it). A mutant touching added or changed lines (no old line) uses the tests that passed through the old-side
  lines of that hunk (a changed hunk: its old line range; a pure addition: the old lines just before and after) ("hunk level") — when there is no such
  test, every test that passed through that file ("file level"); for a file not in the map (a new file after recording) first the tests whose text
  mentions the module name ("name level"), and only when no test mentions it all `--test` commands ("file not in map"). When no test can be selected it is a "gap" — not run and counted separately
  (not added to survived or killed). When the map is stale the caller does not pass `--map` (then all `--test` commands, as without a map)
- `--baseline-results {name: pass|fail}` hands over results already obtained in the repository. **If any of them is fail, nothing is run and the baseline
  fails** (so a flaky test cannot turn into a pass by rerunning). A given "pass" does not replace the run on the unmutated copy — the result was
  obtained in the repository, and the mutants run in the copy
- With `--stop-over <minutes>` the run stops before the mutants (exit code 4) when even the low estimate is longer. The estimate can always be made:
  every test the mutants use has just been run once in the copy (step 5), and a test without a measured time gets the time measured there.
  It counts measured test time only — copying the repository and starting the processes come on top
- The number of test runs and the elapsed time are written into the result
- The result also names, per file, the added hunks (lines only added, nothing removed) in which no mutant could be built at all
  (`added_without_mutants` — an `import`, a comment, a blank line, a docstring): the report does not count such lines as a gap

Exit code: 0 no mutant survived · 1 survived or gap present · 2 baseline failed, or a target file could not be read as Python · 4 stopped by `--stop-over`
"""
import argparse
import ast
import contextlib
import hashlib
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import impact  # noqa: E402 — the one place for run_command (ends the whole process group on timeout)

# the last two alternatives are pytest: a "FAILED tests/..." line and the final line "=== 2 failed, 10 passed in ... ==="
DEFAULT_FAIL_PATTERN = r"(?m)^  FAIL  |^(?:FAIL|ERROR):|^failures: [1-9]|^FAILED |^=+ .*\b[1-9]\d* (?:failed|errors?)\b"
COMPARE_SWAP = {ast.Gt: ast.GtE, ast.GtE: ast.Gt, ast.Lt: ast.LtE, ast.LtE: ast.Lt, ast.Eq: ast.NotEq, ast.NotEq: ast.Eq,
                ast.In: ast.NotIn, ast.NotIn: ast.In, ast.Is: ast.IsNot, ast.IsNot: ast.Is}
COPY_IGNORE = shutil.ignore_patterns(".git", "__pycache__", "node_modules", ".oqa", "*.pyc")


HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", re.M)


def diff_text(repo, file, range_):
    """Output of `git diff -U0 <HEAD | range> -- file`, asked for the same way whatever the user's git settings say (impact.DIFF_OPTIONS: no colour,
    no external diff program, no text conversion, the prefixes a/ · b/) — with `color.ui always` the hunk lines start with an escape code, were not
    recognised, and the file read as "no changed lines". Stops (SystemExit) when git fails, and when the output is there but is not a diff"""
    args = ["diff"] + impact.DIFF_OPTIONS + ["-U0"] + (["HEAD"] if range_ == "working" else [range_]) + ["--", str(file)]
    done = subprocess.run(["git", "-C", str(repo), "-c", "core.quotepath=false"] + args, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if done.returncode != 0:
        raise SystemExit("git diff failed: " + done.stderr.strip()[-300:])
    text = done.stdout or ""
    if text.strip() and not text.startswith("diff --git "):
        raise SystemExit("git diff output could not be read (it does not start with `diff --git`) — the changed lines of %s are unknown, not none: %s" % (file, text.strip().splitlines()[0][:120]))
    return text


def hunks(text):
    """[(old start, old count, new start, new count)] of a diff text"""
    return [tuple(int(m.group(i)) if m.group(i) is not None else 1 for i in (1, 2, 3, 4)) for m in HUNK.finditer(text)]


def changed_lines(repo, file, range_):
    """Set of changed (new side) line numbers of the file. None (every line) when the range is "all\""""
    if range_ == "all":
        return None
    text = diff_text(repo, file, range_)
    lines = set()
    for _old_start, _old_count, start, count in hunks(text):
        lines.update(range(start, start + count))
    if not text.strip():
        # a new (untracked) file has an empty diff — then the whole file is changed
        tracked = subprocess.run(["git", "-C", str(repo), "-c", "core.quotepath=false", "ls-files", "--error-unmatch", str(file)], capture_output=True)
        if tracked.returncode != 0:
            return None
    return lines


def added_ranges(repo, file, range_):
    """[[first, last]] — the new-side line range of every hunk that only adds lines (nothing removed), by the same rule and from the same diff as
    impact.changed_old_lines gives them to the report. Empty for range "all" and for a file without such a hunk (also an untracked new file:
    the report treats that as one whole range itself)"""
    if range_ == "all":
        return []
    return [[new_start, new_start + new_count - 1] for _old_start, old_count, new_start, new_count in hunks(diff_text(repo, file, range_)) if not old_count and new_count]


def old_line_mapper(repo, file):
    """A function that maps a line number of the current file (new line) to the HEAD side line number (old line). Added or changed lines give None.
    None for an untracked new file (cannot be matched)"""
    tracked = subprocess.run(["git", "-C", str(repo), "-c", "core.quotepath=false", "ls-files", "--error-unmatch", str(file)], capture_output=True)
    if tracked.returncode != 0:
        return None
    file_hunks = hunks(diff_text(repo, file, "working"))

    def old_line(new):
        shift = 0
        for old_start, old_count, new_start, new_count in file_hunks:
            if new_count and new_start <= new < new_start + new_count:
                return None                                   # an added or changed line — it has no old line
            if (new_count and new >= new_start + new_count) or (not new_count and new > new_start):
                shift += old_count - new_count                # after this hunk — shift by what the hunk added or removed
            else:
                break
        return new + shift

    def hunk_old_lines(new):
        """Old-side lines of the hunk holding an added or changed line (no old line) — a changed hunk: its old line range; a pure addition:
        the old lines just before and after. Same rule as impact.changed_old_lines. Empty set when the line is not inside a hunk"""
        for old_start, old_count, new_start, new_count in file_hunks:
            if new_count and new_start <= new < new_start + new_count:
                return set(range(old_start, old_start + old_count)) if old_count else {old_start, old_start + 1}
        return set()
    old_line.hunk = hunk_old_lines
    return old_line


def pick_tests(map_, file, old_line, spot, default_names, unmapped=()):
    """(selected test names, selection method) — map_: {name: {file: [old lines]}}. Methods: line level · hunk level · file level · file not in map.
    unmapped — tests that are in the test list but not in the map (added after the map was recorded). Which lines they pass through is
    "unknown" → always included (so that unknown is not flattened into "does not pass")"""
    passed = {name: set(files.get(file, [])) for name, files in map_.items()}
    extra = set(unmapped)
    if old_line is None or not any(passed.values()):
        return sorted(set(default_names) | extra), "file not in map"
    news = range(spot["line"], (spot.get("end_line") or spot["line"]) + 1)
    olds = [old_line(n) for n in news]
    if any(n is None for n in olds):
        # a line without an old line is matched through the old-side lines of its hunk (same rule as impact select). When no test passed through
        # those lines it is unknown → file level (not passing the hunk's old lines does not mean not passing this mutant)
        hunk = getattr(old_line, "hunk", None)
        matched = {n for n in olds if n is not None}
        for new, old in zip(news, olds):
            if old is None and hunk:
                matched |= hunk(new)
        hunk_picked = {name for name, lines in passed.items() if lines & matched}
        if hunk and hunk_picked:
            return sorted(hunk_picked | extra), "hunk level"
        return sorted({name for name, lines in passed.items() if lines} | extra), "file level"
    return sorted({name for name, lines in passed.items() if lines & set(olds)} | extra), "line level"


def _replace(node, new_node, kind):
    return {"line": node.lineno, "end_line": node.end_lineno, "col": node.col_offset, "end_col": node.end_col_offset, "new_text": ast.unparse(new_node), "kind": kind}


def mutants(text, lines):
    """List of (spot to replace, new text, kind) — only nodes that overlap the changed lines"""
    tree = ast.parse(text)
    found = []

    def overlaps(node):
        return lines is None or any(n in lines for n in range(node.lineno, (node.end_lineno or node.lineno) + 1))

    for node in ast.walk(tree):
        # a decorator is replaced by one that hands the function back unchanged. Its lines lie above the `def` line (outside the node's own
        # range), so it is looked at before the overlap test of the node — an added `@cache` line had no mutant and counted as "nothing to mutate"
        for decorator in getattr(node, "decorator_list", None) or []:
            if overlaps(decorator):
                found.append(_replace(decorator, ast.parse("lambda _same: _same", mode="eval").body, "decorator → identity"))
        if not hasattr(node, "lineno") or not overlaps(node):
            continue
        if isinstance(node, ast.Compare):
            for i, op in enumerate(node.ops):
                if type(op) in COMPARE_SWAP:
                    new = ast.Compare(left=node.left, ops=list(node.ops), comparators=node.comparators)
                    new.ops[i] = COMPARE_SWAP[type(op)]()
                    found.append(_replace(node, new, "compare %s → %s" % (type(op).__name__, type(new.ops[i]).__name__)))
        elif isinstance(node, ast.BoolOp):
            new = ast.BoolOp(op=ast.Or() if isinstance(node.op, ast.And) else ast.And(), values=node.values)
            found.append(_replace(node, new, "and ↔ or"))
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            found.append(_replace(node, node.operand, "remove not"))
        elif isinstance(node, ast.Constant) and isinstance(node.value, bool):
            found.append(_replace(node, ast.Constant(not node.value), "True ↔ False"))
        elif isinstance(node, ast.Constant) and isinstance(node.value, int) and not isinstance(node.value, bool):
            found.append(_replace(node, ast.Constant(node.value + 1), "number +1"))
        if isinstance(node, (ast.If, ast.While)) and overlaps(node.test):
            found.append(_replace(node.test, ast.UnaryOp(op=ast.Not(), operand=node.test), "negate condition"))
        if isinstance(node, ast.Return) and node.value is not None and not (isinstance(node.value, ast.Constant) and node.value.value is None):
            found.append(_replace(node.value, ast.Constant(None), "return value → None"))
        # a line that is only a print is not removed — only a message disappears, so it is almost never killed, and killing it would need tests that match every wording
        print_line = isinstance(node, ast.Expr) and isinstance(getattr(node, "value", None), ast.Call) and isinstance(node.value.func, ast.Name) and node.value.func.id == "print"
        if isinstance(node, (ast.Expr, ast.Assign, ast.AugAssign, ast.AnnAssign)) and not print_line and not (
                isinstance(node, ast.Expr) and isinstance(getattr(node, "value", None), ast.Constant) and isinstance(node.value.value, str)):
            found.append(_replace(node, ast.Pass(), "statement → pass"))
        # statements that end or leave a path. They had no mutant at all, so an added `raise` that no test reaches counted as "nothing to mutate".
        # `break` is not one of them: without it a `while True:` never ends, and a mutant that only times out cannot be told from a broken run
        if isinstance(node, (ast.Raise, ast.Continue, ast.Delete)) or (
                isinstance(node, ast.Return) and (node.value is None or (isinstance(node.value, ast.Constant) and node.value.value is None))):
            found.append(_replace(node, ast.Pass(), "statement → pass"))
    return found


# The rule: every kind of statement either has an operator in mutants() or is in this list — a hunk is "nothing to mutate" only when it holds
# nothing but these (and comments, blank lines, docstrings, print lines). What is listed and why:
#   imports · `pass` · `global` / `nonlocal`      nothing a test could observe on that line
#   the header of `def` · `class` · `try` · `with` · `for`      what runs is the body, made of statements of its own (a decorator is not part
#                                                                of the header: it has an operator)
#   `assert` · `break`      no operator: taking an assert out changes nothing while it holds, and taking a `break` out makes loops endless
# The last two rows are a limit, not a proof — such a line added without a test is not flagged (README "Limits": what no operator can break is not probed)
INERT = (ast.Import, ast.ImportFrom, ast.Pass, ast.Global, ast.Nonlocal, ast.Assert, ast.Break, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
         ast.Try, ast.With, ast.AsyncWith, ast.For, ast.AsyncFor)


def _inert(node):
    if isinstance(node, INERT):
        return True
    value = getattr(node, "value", None)
    if isinstance(node, ast.Expr) and isinstance(value, ast.Constant) and isinstance(value.value, str):
        return True                                         # a docstring
    return isinstance(node, ast.Expr) and isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id == "print"      # a print line is never mutated (see mutants)


def inert_only(text, first, last):
    """True when no statement that can change behaviour has its own lines inside first..last — only imports, comments, blank lines, docstrings,
    print lines, `pass` and the like. The own lines of a statement with a body are its header — from its first line to the end of its
    condition (`if` · `while`) or subject (`match`); a comment or blank line between the header and the body is not part of it, and the body's
    statements are judged one by one. "No mutant fell into the hunk" is not this: a statement the operators cannot touch is not nothing"""
    for node in ast.walk(ast.parse(text)):
        if not isinstance(node, ast.stmt) or _inert(node):
            continue
        if isinstance(getattr(node, "body", None), list):
            head = getattr(node, "test", None) or getattr(node, "subject", None)
            end = getattr(head, "end_lineno", None) or node.lineno
        else:
            end = node.end_lineno or node.lineno
        if node.lineno <= last and max(end, node.lineno) >= first:
            return False
    return True


def split_lines(text, keepends=False):
    """Split lines by the same rule as ast (\\r\\n · \\r · \\n only). str.splitlines also splits at \\x0c · \\x1c · \\u2028, which disagrees with ast line numbers"""
    parts = re.split(r"(\r\n|\r|\n)", text)
    lines = [parts[i] + (parts[i + 1] if keepends and i + 1 < len(parts) else "") for i in range(0, len(parts), 2)]
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def apply(text, spot):
    """Replace only that node (line / column range) of the text with the new text. The indentation of later lines of a multi-line node stays"""
    lines = split_lines(text, keepends=True)
    before = "".join(lines[:spot["line"] - 1]) + _byte_col(lines[spot["line"] - 1], spot["col"])[0]
    after = _byte_col(lines[spot["end_line"] - 1], spot["end_col"])[1] + "".join(lines[spot["end_line"]:])
    return before + spot["new_text"] + after


def _byte_col(line_text, col):
    """ast column numbers are UTF-8 byte offsets — on a line with non-ASCII characters cut by bytes and convert back to text"""
    data = line_text.encode("utf-8")
    return data[:col].decode("utf-8", "replace"), data[col:].decode("utf-8", "replace")


def decode(data):
    """Child output — read as UTF-8, otherwise in this system's legacy encoding (on Windows a Python running without PYTHONUTF8 prints in the local code page — the failure mark got garbled and the pattern missed it)"""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        import locale
        return data.decode(locale.getpreferredencoding(False) or "utf-8", "replace")


run_count = {"n": 0}                                       # how many times a test command was run in this execution — the cost of the run, written into the result


_run_count_lock = threading.Lock()


def run_tests(commands, cwd, fail_pattern, timeout, stop_on_kill=False, ran=None):
    """(failure mark found, all exit codes 0, timed out, output tail). commands are strings (command) or (command, fail pattern of that test only | None[, lock of an exclusive test | None]).
    stop_on_kill — when running a mutant: the moment a failure line appears that test is ended (read line by line) and the remaining tests are not run. The yardstick for "killed" (a failure line) is the same.
    A mutant that is not killed runs all selected tests to the end, as before. The baseline (original) runs without stop_on_kill — it has to run to the end to see a pass.
    The temporary location of the tests (TEMP · TMP · TMPDIR) is pointed at a folder made by this call and removed at the end — a test ended by a failure line or
    a timeout cannot delete its own temporary folder"""
    tmp = tempfile.mkdtemp(prefix="oqa_mutate_tmp_")
    try:
        return _run_tests(commands, cwd, fail_pattern, timeout, stop_on_kill, ran, tmp)
    finally:
        shutil.rmtree(tmp, onerror=_unlock_and_remove)
        if os.path.isdir(tmp):                                # the process that was just ended may still hold files — once more a moment later
            time.sleep(0.5)
            shutil.rmtree(tmp, onerror=_unlock_and_remove)


def _run_tests(commands, cwd, fail_pattern, timeout, stop_on_kill, ran, tmp):
    failed, all_zero, timed_out, tail = False, True, False, ""
    for item in commands:
        command, own_pattern, lock = (tuple(item) + (None, None))[:3] if isinstance(item, tuple) else (item, None, None)
        with _run_count_lock:
            run_count["n"] += 1
        if ran is not None:
            ran.append(command)                              # the tests actually run in this call (fewer than selected when it stops on a kill)
        pattern = own_pattern or fail_pattern                # the pattern of this test — the default pattern (fail_pattern) is not overwritten (otherwise the next test is checked with the previous test's own pattern)
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "TEMP": tmp, "TMP": tmp, "TMPDIR": tmp}
        with (lock or contextlib.nullcontext()):
            if stop_on_kill:
                code, out, err, hit = impact.run_streaming(command, cwd, timeout, env, pattern)
                if hit:
                    return True, False, False, hit              # killed — the rest of this test and the later tests do not run
                if code is not None and code != 0:
                    # ended with an error without a failure line (exception, assert) — that is killed. This call is the mutant path (stop_on_kill), and the original
                    # was first seen to pass in the same copy (baseline), so the mutant is to blame. If it counted as "crashed", a mutant that removes an assignment
                    # (always a NameError) would run every attached test to the end and end up as "cannot judge". A timeout (code None) stays "crashed" below
                    return True, False, False, "ended with an error (exit code %s) — %s" % (code, ((decode(out) + decode(err)).strip().splitlines() or [""])[-1][:120])
            else:
                code, out, err = impact.run_command(command, cwd, timeout, env)   # on timeout the whole process group is ended
        if code is None:
            timed_out, all_zero = True, False
            continue
        output = decode(out) + decode(err)
        if re.search(pattern, output):
            failed = True
            tail = next((ln.strip() for ln in output.splitlines() if re.search(pattern, ln)), "")[:160]
        if code != 0:
            all_zero = False
            if not tail:
                tail = (output.strip().splitlines() or [""])[-1][:160]
    return failed, all_zero, timed_out, tail


def _unlock_and_remove(func, path, _info):
    """onerror for shutil.rmtree — unlock a read-only file (files inside a .git made by a test) and delete again. If that still fails, move on (do not block the next run).
    Deleting with ignore_errors only left copies holding such files in the temp folder"""
    try:
        os.chmod(path, 0o700)
        func(path)
    except OSError:
        pass


def file_hash(text):
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def classify(failed, all_zero, timed_out):
    if failed:
        return "killed"
    if timed_out or not all_zero:
        return "crashed"
    return "survived"


def auto_jobs():
    """Copies to run at the same time when the user does not say: half the logical CPUs, at most 8, at least 1.
    Each copy runs a whole test command, so using every CPU slows the tests themselves down."""
    return max(1, min(8, (os.cpu_count() or 2) // 2))


def jobs_arg(value):
    """`--jobs` value — a positive number, or `auto`"""
    if str(value).strip().lower() == "auto":
        return auto_jobs()
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("--jobs must be 1 or more, or auto")
    return number


def max_arg(value):
    """`--max` value — a whole number, 1 or more. With 0 no mutant would run and the result would read as "0 mutants generated\""""
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("--max must be 1 or more (a whole number)")
    if number < 1:
        raise argparse.ArgumentTypeError("--max must be 1 or more")
    return number


def minutes_text(seconds):
    return "%.0fs" % seconds if seconds < 90 else "%.0f min" % (seconds / 60) if seconds < 5400 else "%.1f h" % (seconds / 3600)


def estimate(todo, seconds_table, jobs):
    """(low seconds, high seconds, tests without a measured time) for the mutants about to run.
    low: every mutant is killed by the first test in its run order (it stops there); high: no mutant is killed, so every selected test runs.
    Both are divided by the number of jobs. Tests without a measured time are left out of the sums and counted separately — they are not taken as zero cost."""
    low = high = 0.0
    untimed = set()
    for *_, spot in todo:
        timed = [seconds_table[n] for n in spot["selected"] if n in seconds_table]
        untimed.update(n for n in spot["selected"] if n not in seconds_table)
        if timed:
            low += timed[0]
            high += sum(timed)
    return low / jobs, high / jobs, len(untimed)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Break changed lines on purpose and count whether the tests bite")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--files", nargs="+", required=True)
    parser.add_argument("--test", action="append", required=True, help="test command to run from the repository root (may be given several times)")
    parser.add_argument("--range", default="working")
    parser.add_argument("--fail-pattern", default=DEFAULT_FAIL_PATTERN, help="pattern (regular expression) that means failure in the test output")
    parser.add_argument("--max", type=max_arg, default=60, help="most mutants to run (1 or more) — with more generated, an evenly spread sample runs")
    parser.add_argument("--timeout", type=int, default=180, help="time limit of one command (seconds)")
    parser.add_argument("--json")
    parser.add_argument("--map", help="impact map (.oqa/impact-map.json) — when given, only the tests that passed through that line run for each mutant (do not pass a stale map)")
    parser.add_argument("--tests-file", help="test list [{name, command, fail_pattern?}] — used to turn the test names of the map into commands")
    parser.add_argument("--baseline-results", help="results already obtained in the repository {name: pass|fail} — a fail stops the run (baseline failed); a pass is still checked once on the unmutated copy")
    parser.add_argument("--jobs", type=jobs_arg, default=1, help="in how many copies mutants run at the same time (a number, or auto = half the CPUs up to 8). A test with \"exclusive\": true in the test list runs only one at a time")
    parser.add_argument("--stop-over", type=float, help="minutes — when even the low estimate (every mutant killed by its first test; measured test time only) is longer than this, stop before running the mutants (exit code 4) so the caller can ask")
    args = parser.parse_args(argv)
    repo = Path(args.repo).resolve()
    run_count["n"] = 0
    started_all = time.time()
    # tests are handled by name — a --test command is its name in the test list when listed, otherwise the command text itself is the name
    table = json.loads(Path(args.tests_file).read_text(encoding="utf-8-sig")) if args.tests_file else []
    # "exclusive" — tests that use the same port or the same outside resource. A lock is passed along so that only one runs at a time even with several copies (so that a failure caused by colliding runs is not counted as "killed")
    command_table = {t["name"]: (t["command"], t.get("fail_pattern"), threading.Lock() if t.get("exclusive") else None) for t in table}
    seconds_table = {t["name"]: t["seconds"] for t in table if isinstance(t.get("seconds"), (int, float))}      # time measured on the original — fast tests run first (because it stops on a kill)
    case_table = {t["name"]: (t["case_base"], t["case_name"]) for t in table if t.get("case_base") and t.get("case_name")}

    def grouped(names):
        """[(test names, command to run)] — the cases of one test file are grouped into a single command `base --case a,b,c` (at the position of the first case). Starting a new
        program for every case costs seconds per case (program and temporary repository setup), so a surviving mutant spent minutes on a handful of cases.
        The set of cases run and the failure yardstick are the same"""
        out, position = [], {}
        for name in names:
            command, own_pattern, lock = command_table[name]
            if name in case_table:
                base, case_name = case_table[name]
                if base in position:
                    out[position[base]][0].append(case_name)
                    out[position[base]][4].append(name)
                    continue
                position[base] = len(out)
                out.append(([case_name], base, own_pattern, lock, [name]))
            else:
                out.append((None, command, own_pattern, lock, [name]))
        return [(members, (("%s --case %s" % (base, ",".join(cases))) if cases else base, own_pattern, lock)) for cases, base, own_pattern, lock, members in out]

    def grouped_commands(names):
        return [command for _, command in grouped(names)]
    name_table = {t["command"]: t["name"] for t in table}
    default_names = []
    for command in args.test:
        name = name_table.get(command, command)
        command_table.setdefault(name, (command, None, None))
        default_names.append(name)
    map_ = json.loads(Path(args.map).read_text(encoding="utf-8"))["tests"] if args.map else None
    if map_ is not None and not args.tests_file:
        raise SystemExit("--map needs --tests-file — it turns the test names of the map into commands")
    unmapped = sorted(t["name"] for t in table if map_ is not None and t["name"] not in map_)      # tests added after the map was recorded — included for every mutant
    baseline_results = json.loads(Path(args.baseline_results).read_text(encoding="utf-8")) if args.baseline_results else {}
    baseline_results = {k: v for k, v in baseline_results.items() if v in ("pass", "fail")}      # a value such as "not in list" is not a result — that test is run on the original

    # Test selection for a file not in the map (a new file after recording) — only the tests whose text (for a case test, that case block) mentions the module name ("name level").
    # Otherwise every mutant of a new file runs every test. A test that does not mention the name may still pass through that file —
    # then only "survived" grows; no false "killed" appears (it errs on the strict side only). When no test mentions the name, all tests as before
    test_text_cache, name_cache = {}, {}

    def test_text(t):
        base = t.get("case_base") or t["command"]
        path_ = next((repo / w.strip("\"'") for w in base.split() if w.strip("\"'").endswith(".py") and (repo / w.strip("\"'")).is_file()), None)
        if path_ is None:
            return None
        if path_ not in test_text_cache:
            try:
                test_text_cache[path_] = path_.read_text(encoding="utf-8", errors="replace")
            except OSError:
                test_text_cache[path_] = None
        text_ = test_text_cache[path_]
        if text_ is None or not t.get("case_name"):
            return text_
        lines_ = text_.splitlines()
        heads = [i for i, ln in enumerate(lines_) if ln.startswith("# case: ")]
        for i, start in enumerate(heads):
            if lines_[start][len("# case: "):].strip() == t["case_name"]:
                return "\n".join(lines_[start:(heads[i + 1] if i + 1 < len(heads) else len(lines_))])
        return None

    def by_name(rel):
        stem = rel.stem
        if stem not in name_cache:
            name_cache[stem] = {t["name"] for t in table if stem in (test_text(t) or "")}
        return name_cache[stem]

    todo, generated_by_file, added_without_mutants, inert_files, bom_files = [], {}, {}, [], set()
    for f in args.files:
        path = (repo / f).resolve() if not Path(f).is_absolute() else Path(f).resolve()
        rel = path.relative_to(repo)
        # A target file that cannot be read as Python is not "0 mutants": nothing is counted, for any file, and the result names the file and the problem
        problem, text, lines, spots = None, None, None, []
        try:
            text = path.read_text(encoding="utf-8")
            if text.startswith("\ufeff"):
                # a UTF-8 byte order mark (editors on Windows write it). Python runs such a file, but `ast.parse` of a text that still holds the
                # mark fails — every change in the file was "cannot mutate … U+FEFF" for good. Line numbers are the same without it
                text = text[1:]
                bom_files.add(rel)
        except UnicodeDecodeError:
            problem = "is not valid UTF-8"
        except OSError as error:
            problem = "cannot be read (%s)" % (error.strerror or error.__class__.__name__)
        if problem is None:
            lines = changed_lines(repo, rel, args.range)
            try:
                spots = mutants(text, lines)
            except (SyntaxError, ValueError) as error:      # ValueError: a null byte in the source (older Python versions)
                problem = "does not parse as Python (%s%s)" % (getattr(error, "msg", None) or error, (", line %s" % error.lineno) if getattr(error, "lineno", None) else "")
        if problem is not None:
            basis = "%s %s" % (rel.as_posix(), problem)
            print("cannot mutate — %s. No mutant is counted (this is not \"0 mutants generated\")" % basis)
            if args.json:
                Path(args.json).write_text(json.dumps({"baseline": "not run", "generated": None, "basis": basis, "mutants": [], "test_runs": 0, "seconds": 0},
                                                      ensure_ascii=False, indent=1), encoding="utf-8")
            return 2
        generated_by_file.setdefault(rel.as_posix(), 0)
        old_line = old_line_mapper(repo, rel) if map_ is not None else None
        candidates = []                                     # the spots of this file that became a mutant (before --max thins them out)
        for spot in spots:
            new = apply(text, spot)
            try:
                compile(new, str(rel), "exec")
            except SyntaxError:
                continue                                    # a mutant that cannot be built — not counted
            if new != text:
                if map_ is None:
                    selected, method = list(default_names), "all"
                else:
                    selected, method = pick_tests(map_, rel.as_posix(), old_line, spot, default_names, unmapped)
                    if method == "file not in map":
                        named = by_name(rel)
                        if named:
                            selected, method = sorted(named | set(unmapped)), "name level"
                    selected = [n for n in selected if n in command_table]          # a name not in the test list (a deleted test) cannot be run
                # Run order (the set of selected tests is unchanged): tests not in the map first (new tests added after recording — most likely to look at the new code), then fast tests first.
                # It stops on a kill, so the order decides the cost — a mutant of a new file should not run dozens of old tests before it reaches the new test
                selected = sorted(selected, key=lambda n: (n not in unmapped, seconds_table.get(n, float("inf")), n))
                todo.append((rel, text, new, dict(spot, selected=selected, method=method)))
                generated_by_file[rel.as_posix()] += 1
                candidates.append(spot)
        # The added hunks of this file that no candidate mutant overlaps and that hold nothing but inert lines (an import, a comment, a blank
        # line, a docstring — see inert_only). The report needs it said: such a hunk has no mutant to be killed, and without this it stayed a gap
        # whatever the tests did. "No candidate" alone is not enough: a statement no operator can touch would be confirmed without any test
        added_without_mutants[rel.as_posix()] = [r for r in added_ranges(repo, rel, args.range)
                                                 if not any(spot["line"] <= r[1] and (spot.get("end_line") or spot["line"]) >= r[0] for spot in candidates)
                                                 and inert_only(text, r[0], r[1])]
        ranges_now = added_ranges(repo, rel, args.range)
        only_added = lines is not None and bool(ranges_now) and all(any(first <= n <= last for first, last in ranges_now) for n in lines)
        if not candidates and (inert_only(text, 1, 10 ** 9) or (only_added and len(added_without_mutants[rel.as_posix()]) == len(ranges_now))):
            # nothing in this file's change can be mutated or checked: the whole file is inert (empty, imports, docstrings, bare definitions), or
            # the change only adds lines and every added hunk is inert (an `import` added to an existing file). Without this the same report said
            # "confirmed — nothing to mutate" for the lines and "generated 0 — unknown" for the file
            inert_files.append(rel.as_posix())
    generated = len(todo)
    if len(todo) > args.max:
        # cutting the first N crowds the front lines of the file — pick evenly by skipping
        print("%d mutants, running only %d picked evenly (--max)" % (len(todo), args.max))
        todo = [todo[round(n * (len(todo) - 1) / max(args.max - 1, 1))] for n in range(args.max)]   # both ends are included
    if not todo:
        print("0 mutants generated — no changed lines or no node to mutate (0 does not mean \"no weak tests\")")
        result = {"baseline": "not run", "generated": 0, "generated_by_file": generated_by_file, "added_without_mutants": added_without_mutants, "inert_files": inert_files, "mutants": [], "test_runs": 0, "seconds": 0}
        if args.json:
            Path(args.json).write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        return 0

    # Baseline: a given "fail" stops here (no rerun). Otherwise every test the mutants will use is run once on the unmutated copy — whether or not a result was given
    used_names = sorted({n for *_, spot in todo for n in spot["selected"]})
    given_failures = sorted(k for k, v in baseline_results.items() if v == "fail")      # from all given results — a failure of a test the mutants do not use is a baseline failure too

    def write_result(result, **extra):
        if args.json:
            Path(args.json).write_text(json.dumps(dict(result, generated_by_file=generated_by_file, added_without_mutants=added_without_mutants, inert_files=inert_files,test_runs=run_count["n"], seconds=round(time.time() - started_all, 1),
                                                       line_level=map_ is not None, **extra), ensure_ascii=False, indent=1), encoding="utf-8")

    if given_failures:
        print("the baseline results contain a failure (%s) — not rerun and mutants are not counted" % ", ".join(given_failures))
        write_result({"baseline": "fail", "generated": generated, "basis": "baseline results failed: " + ", ".join(given_failures), "mutants": []})
        return 2
    jobs = max(1, min(args.jobs, len(todo)))
    works = [Path(tempfile.mkdtemp(prefix="oqa_mutate_")) / repo.name for _ in range(jobs)]
    work = works[0]
    baseline = {n: baseline_results[n] for n in used_names if n in baseline_results}      # the results obtained in the repository stay as given — a caller reads them as results of the repository
    try:                                                   # the copy is inside too — the temporary folders are removed even when copying fails
        for w in works:
            shutil.copytree(repo, w, ignore=COPY_IGNORE)
        started = time.time()
        # Every test the mutants will use is run once, unmutated, in the first copy — also a test whose result was given. A given result was obtained in the
        # repository; the copy leaves out .git · .oqa · node_modules, so a test that passes there can fail here for a reason that has nothing to do with a
        # mutant. Such a test would "fail" on every mutant and every mutant would be counted as killed. (The cases of one test file run as one command, as for the mutants)
        baseline_groups = grouped(used_names)
        for members, command in baseline_groups:
            group_started = time.time()
            failed, all_zero, timed_out, tail = run_tests([command], work, args.fail_pattern, args.timeout)
            # A test without a measured time (for example one added after the map was recorded) gets the time just measured here,
            # shared evenly among the cases of one command — so the estimate below does not have to give up on a new test
            for name in members:
                seconds_table.setdefault(name, (time.time() - group_started) / len(members))
            if failed or not all_zero:
                tail = tail or ("timeout" if timed_out else "")
                label = ", ".join(members)[:300]
                if all(baseline_results.get(n) == "pass" for n in members):
                    basis = "passes in the repository but fails in the copy: %s — %s" % (label, tail)
                    print("baseline failed — %s. The mutants run in a copy that leaves out .git, .oqa, node_modules, __pycache__; mutants are not counted" % basis)
                else:
                    basis = "%s: %s" % (label, tail)
                    print("baseline failed (the original already fails) — mutants are not counted: %s %s" % (label, tail))
                    if len(members) == 1:
                        baseline[members[0]] = "fail"         # (with several cases in one command it is not known which one failed — none is marked)
                write_result({"baseline": "fail", "generated": generated, "basis": basis, "mutants": [], "baseline_results": baseline, "baseline_runs": run_count["n"]})
                return 2
            for name in members:
                baseline.setdefault(name, "pass")
        baseline_seconds, baseline_runs = time.time() - started, run_count["n"]
        print("baseline passed (%.1fs · tests run unmutated in the copy %d (results given %d) · commands run %d) · %d mutants"
              % (baseline_seconds, len(used_names), len([n for n in used_names if n in baseline_results]), baseline_runs, len(todo)))
        # How long this will take, said before it starts — a run nobody sized can turn out to be hours. Every used test has a time by now (the list's,
        # or the one just measured in the copy), so the estimate is always made. It is the measured test time only
        low, high, _untimed = estimate(todo, seconds_table, jobs)
        estimate_text = ("estimate for %d mutants with %d job(s): about %s when every mutant is killed by its first test, up to %s when none is killed"
                         " (test time only — copying and start-up come on top)" % (len(todo), jobs, minutes_text(low), minutes_text(high)))
        print(estimate_text, flush=True)
        if args.stop_over is not None and low > args.stop_over * 60:
            print("stopped before running the mutants — the low estimate is over %g min. Run again without the limit to go ahead, or make the tests faster first" % args.stop_over)
            write_result({"baseline": "pass", "generated": generated, "stopped": estimate_text, "mutants": [], "baseline_results": baseline, "baseline_runs": baseline_runs})
            return 4
        # one mutant at a time per copy — a free copy is taken from the queue and put back. Results keep the order of generation (same order as with one job)
        free_copies = queue.Queue()
        for w in works:
            free_copies.put(w)
        print_lock = threading.Lock()

        def one_mutant(item):
            rel, original, mutated, spot = item
            place = {k: v for k, v in spot.items() if k not in ("selected", "method")}
            ran = []
            if not spot["selected"]:
                verdict, tail = "gap", "no test passed through this mutant's location (%s)" % spot["method"]
            else:
                w = free_copies.get()
                try:
                    mark = "\ufeff" if rel in bom_files else ""      # the file keeps the byte order mark it had
                    (w / rel).write_text(mark + mutated, encoding="utf-8")
                    try:
                        failed, all_zero, timed_out, tail = run_tests(grouped_commands(spot["selected"]), w, args.fail_pattern, args.timeout, stop_on_kill=True, ran=ran)
                    finally:
                        (w / rel).write_text(mark + original, encoding="utf-8")
                finally:
                    free_copies.put(w)
                verdict = classify(failed, all_zero, timed_out)
            # For a mutant that was not killed, show the line as it was mutated — the kind alone ("number +1") does not say which value on the line changed
            mutated_line = None
            if verdict != "killed":
                rows = split_lines(mutated)
                mutated_line = rows[spot["line"] - 1].strip()[:120] if 0 < spot["line"] <= len(rows) else None
            with print_lock:
                print("%-8s %s:%d  %s  [%s %d]  %s%s" % (verdict, rel.as_posix(), spot["line"], spot["kind"], spot["method"], len(spot["selected"]), tail[:80] if verdict != "killed" else "",
                                                       ("  | mutated line: " + mutated_line) if mutated_line else ""), flush=True)
            return {"file": rel.as_posix(), "line": spot["line"], "kind": spot["kind"], "verdict": verdict, "basis": tail, "spot": place, "mutated_line": mutated_line,   # spot — the test draft tool rebuilds the same mutant from it
                    "file_hash": file_hash(original), "tests": spot["selected"], "selection": spot["method"], "ran": len(ran)}        # when the file changes later the spot shifts — the test draft tool compares the hash. ran = number of tests actually run (stops on a kill)
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            results = list(pool.map(one_mutant, todo))
    finally:
        for w in works:
            shutil.rmtree(w.parent, onerror=_unlock_and_remove)
    counts = {k: sum(1 for r in results if r["verdict"] == k) for k in ("killed", "survived", "crashed", "gap")}
    print("\nkilled %d · survived %d · crashed %d · gap %d (total %d) · test runs %d · %.0fs"
          % (counts["killed"], counts["survived"], counts["crashed"], counts["gap"], len(results), run_count["n"], time.time() - started_all))
    # baseline_runs — how many test commands were run on the unmutated copy (test_runs = baseline_runs + the runs on mutants)
    write_result({"baseline": "pass", "generated": generated, "counts": counts, "mutants": results, "baseline_results": baseline, "baseline_runs": baseline_runs})
    return 0 if counts["survived"] == 0 and counts["gap"] == 0 else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
