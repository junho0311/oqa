# -*- coding: utf-8 -*-
"""**Collects the temp folders a test makes inside one folder for that test, and removes it whole at the end.** `import _harness` at the very top of a test file.

Tests that call mkdtemp · NamedTemporaryFile(delete=False) and never clean up leave thousands of folders in the system temp folder.
Fixing them one place at a time misses some, so the place is changed instead: this test process's temp place (tempfile.tempdir) and the
environment variables TEMP · TMP are pointed at a folder for this run only — the tool processes the test starts inherit the environment
and write into the same folder. When the process ends that folder is removed.
"""
import atexit
import os
import shutil
import stat
import tempfile

place = tempfile.mkdtemp(prefix="oqa_test_")
for _key in ("TEMP", "TMP", "TMPDIR"):
    os.environ[_key] = place
tempfile.tempdir = place


def _unlock_and_remove(func, path, _error):
    """rmtree cannot remove read-only files such as git objects — make them writable and try again"""
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except OSError:
        pass


atexit.register(lambda: shutil.rmtree(place, onerror=_unlock_and_remove))


def _python_on_path():
    """The fixture commands in the tests call `python`. Where that name does not exist (many Linux systems only have
    `python3`) point it at the interpreter running the tests, through a link in this run's temp folder. An existing
    `python` is left alone."""
    import sys
    if os.name == "nt" or shutil.which("python"):
        return
    shim = os.path.join(place, "bin")
    os.makedirs(shim, exist_ok=True)
    os.symlink(sys.executable, os.path.join(shim, "python"))
    os.environ["PATH"] = shim + os.pathsep + os.environ.get("PATH", "")


_python_on_path()


# ── Record at the end of the test ──
# When the test ends it records its own result (test file name · exit code) as one evidence line.
# Failing to record does not change the test result (passed over quietly). The exit code is the value given to sys.exit; an uncaught exception is 1.
# The exit code starts as None (not known): only the wrapped sys.exit, the exception hook and run_cases set it. A test that ends another way
# (a bare `raise SystemExit(1)`, running off its end) told the harness nothing, so its line says "unknown" — with a default of 0 such a
# failing test was recorded as a pass
import sys as _sys  # noqa: E402
from pathlib import Path as _Path  # noqa: E402

NOT_KNOWN = "the test did not end through sys.exit or run_cases, so its exit code is not known"
_end = {"code": None}
_old_exit, _old_hook = _sys.exit, _sys.excepthook


def _exit(code=0):
    _end["code"] = code if isinstance(code, int) else (0 if code is None else 1)
    _old_exit(code)


def _hook(kind, value, trace):
    if not issubclass(kind, SystemExit):
        _end["code"] = 1
    _old_hook(kind, value, trace)


def _test_end():
    _evidence()


# ── Running case by case ──
# When one test file runs dozens of cases in turn in one process, checking a single mutant costs the time of all of them.
# Split the test file into cases with `# case: name` lines and call `raise SystemExit(_harness.run_cases(__file__, globals(), finish))` at the end of the head (shared setup):
#   python x_test.py                  every case (same as before)
#   python x_test.py --case a,b       only those cases
#   python x_test.py --list-cases     the case names, one per line
# Listed in the test list (.oqa/tests.json) with "cases": true, impact.py expands it into one test per case — the map and mutant selection become per case.
# Cases must not use each other's variables (each sets up its own) — only what the head made is shared
CASE_MARKER = "# case: "


def split_cases(text):
    """[(name, 0-based line number of the piece's first line, piece text)] — from the first `# case: ` line. Empty list when there is no marker"""
    lines = text.splitlines(keepends=True)
    places = [i for i, line in enumerate(lines) if line.startswith(CASE_MARKER)]
    out = []
    for n, start in enumerate(places):
        end = places[n + 1] if n + 1 < len(places) else len(lines)
        out.append((lines[start][len(CASE_MARKER):].strip(), start, "".join(lines[start:end])))
    return out


def _case_args():
    return [b for a in (_sys.argv[_sys.argv.index("--case") + 1].split(",") if "--case" in _sys.argv[:-1] else []) for b in [a.strip()] if b]


def _case_suffix():
    """Suffix for the name in the evidence record — ":a,b" when cases were picked (recorded per case, so one case's failure is not hidden by another case's pass)"""
    try:
        picked = _case_args()
        return (":" + ",".join(picked)) if picked else ""
    except Exception:                                    # noqa: BLE001
        return ""


def run_cases(file, namespace, finish=None):
    """Runs the case pieces of the test file in turn in that file's namespace and returns the exit code (the value of finish() when given).
    With an unknown case name nothing is run and the answer is 2 — so that it does not run 0 cases and end as a "pass".

    The result is noted here for the evidence line: the usual ending `raise SystemExit(run_cases(...))` does not pass through
    sys.exit, so without this a failing case-based test was recorded as a pass. A call that ran no case (the case list, a
    wrong case name) leaves no evidence at all — nothing ran, so nothing passed or failed"""
    code, ran = _run_cases(file, namespace, finish)
    _end["code"] = code if isinstance(code, int) else (0 if code is None else 1)
    _end["skip"] = not ran
    return code


def _run_cases(file, namespace, finish):
    """(exit code, whether any case was run)"""
    pieces = split_cases(_Path(file).read_text(encoding="utf-8"))
    names = [name for name, _, _ in pieces]
    if len(set(names)) != len(names):
        print("duplicate case names: %s" % sorted({name for name in names if names.count(name) > 1}))
        return 2, False
    if "--list-cases" in _sys.argv:
        print("\n".join(["# case list"] + names))        # header line — so the ordinary output of a test that does not know cases is not read as case names (impact.py looks at it)
        return 0, False
    picked = _case_args()
    if "--case" in _sys.argv and not picked:
        print("no case name after --case")
        return 2, False
    missing = [name for name in picked if name not in names]
    if missing:
        print("no such case: %s (see --list-cases)" % ", ".join(missing))      # ASCII only: a test's output may go to a pipe in a legacy code page
        return 2, False
    if not pieces:
        print("no `# case: ` marker in this file - run_cases has no case to run")      # nothing ran, so nothing passed: not exit code 0, and no evidence
        return 2, False
    ran = 0
    for name, start, piece in pieces:
        if picked and name not in picked:
            continue
        exec(compile("\n" * start + piece, str(file), "exec"), namespace)      # line numbers match the original file (error lines · map)
        ran += 1
    if not ran:                                          # "ran" is said only when a case piece was really executed
        print("no case was run")
        return 2, False
    return (finish() if finish else 0), True


def _repo_root():
    """Root of the git repository this test file is in — None when outside git, as in a mutation copy"""
    try:
        base = _Path(_sys.argv[0]).resolve().parent
        return next((p for p in [base] + list(base.parents) if (p / ".git").exists()), None)
    except (OSError, IndexError):
        return None


def _fingerprint_now():
    try:
        _sys.path.insert(0, str(_Path(__file__).resolve().parents[1] / "scripts"))
        import evidence
        root = _repo_root()
        return evidence.fingerprint(root) if root else None
    except Exception:                                    # noqa: BLE001
        return None


def _evidence():
    """Records this test's result together with **the fingerprint of the code that ran** in that repository's evidence file (Core compares it with the fingerprint now).
    An exit code nobody told the harness (None) is recorded as "unknown" with the reason as basis — never as a pass.
    When the fingerprint differs between start and end (the code changed while running) it is not known which code the result belongs to → fingerprint "unknown" (not used as evidence). Outside git (a copy) nothing is recorded"""
    try:
        root = _repo_root()
        if root is None or _end.get("skip"):
            return
        import evidence
        end = evidence.fingerprint(root)
        code = _end["code"]
        result, basis = ("unknown", NOT_KNOWN) if code is None else ("pass" if code == 0 else "fail", "exit code %d" % code)
        evidence.record(root, "Regression Test", result, basis,
                        end if end and end == _start_fingerprint else "unknown", "_harness.py", name=_Path(_sys.argv[0]).stem + _case_suffix())
    except Exception:                                    # noqa: BLE001 — evidence is an extra — it does not change the test result
        pass


_start_fingerprint = _fingerprint_now()


_sys.exit, _sys.excepthook = _exit, _hook
atexit.register(_test_end)
