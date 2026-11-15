# -*- coding: utf-8 -*-
"""**Set a repository up for OQA: the state folder, a test list found from the test files, and the ignore lines.**

    python init.py --repo <root> [--force] [--json]

- `.oqa/specs/` is created.
- `.oqa/tests.json` is written from the test files found (`test_*.py`, `*_test.py`), **one entry per test file** —
  mutation runs only the entries whose recorded lines include the mutated line, so one entry for the whole suite
  would make every mutant cost the whole suite. An existing list is kept unless `--force` is given.
- Lines for the machine-local state files are appended to `.gitignore` when missing.

How a test file is run is decided per file and said in the output, never assumed silently. The first rule that fits, in this order:
1. the file imports pytest → `pytest`;
2. the file defines a class deriving from `TestCase` (`unittest.TestCase` or an imported `TestCase`) → `unittest`, or `pytest`
   when the repository is set up for pytest (pytest runs such classes). Importing unittest alone decides nothing —
   `from unittest import mock` above plain `test_` functions is a pytest-style file;
3. the file defines top-level `test_` functions → `pytest`;
4. the file exits by itself at module level (`sys.exit(` / `raise SystemExit(` at the start of a line) and has no `test_`
   function → a plain script, also in a repository set up for pytest (importing it during collection would hit the exit);
5. the repository is set up for pytest → `pytest`;
6. the file has a `__main__` block → a plain script.
A file that fits none of these is listed as "skipped" with the reason.

Test files under a folder that is skipped by its name (vendored code, environments, tool state) are not dropped silently:
each such folder is a "skipped" row with the number of test files left out. Inside git only `.git`, `.oqa`, `__pycache__`,
`node_modules`, `site-packages` and the environment folders are skipped by name (git's listing already leaves ignored
files out, so a tracked `tests/build/test_wheel.py` is listed); outside git `build`, `dist` and `.eggs` are skipped too.
Nothing is executed here; `impact.py record` runs the list and shows which entries pass.
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

# Folder names under which a test file is not a test of this repository. In a git repository git's own listing already leaves ignored files out, so
# only these are skipped by name there; a walked tree (no git) has no such listing, so build output is skipped by name as well
GIT_SKIP_DIRS = {".git", ".oqa", "__pycache__", "node_modules", "site-packages", ".venv", "venv", "env", ".tox", ".nox"}
SKIP_DIRS = GIT_SKIP_DIRS | {"build", "dist", ".eggs"}
LEFT_OUT = "%d test file(s) left out: the folder name `%s` is skipped (vendored code, environments, tool state) — write an entry by hand for a file that is a test of this repository"
TEST_NAME = re.compile(r"^(test_.+|.+_test)\.py$")


def is_test_name(path):
    """Does the file name say "test file" (`test_*.py` · `*_test.py`) — the one naming rule: the list is built from it here, and core.py's plan and
    report.py use it to say that a changed or new file of that name has no entry in `.oqa/tests.json`"""
    return bool(TEST_NAME.match(str(path).replace("\\", "/").rsplit("/", 1)[-1]))


IGNORE_LINES = (".oqa/evidence.jsonl", ".oqa/impact-map.json", ".oqa/core.json", ".oqa/baseline.json")
WHOLE_FOLDER = {".oqa", ".oqa/", ".oqa/*", ".oqa/**"}      # .gitignore lines (leading slash removed) that ignore the whole state folder
PYTEST_CONFIG = (("pytest.ini", None), ("conftest.py", None), ("pyproject.toml", "[tool.pytest"), ("setup.cfg", "[tool:pytest]"), ("tox.ini", "[pytest]"))


def python_name():
    """The interpreter name to write into the commands — `python` where that name exists, else `python3`.
    The list is committed and used on other machines, so a name is written rather than this machine's full path."""
    return "python" if shutil.which("python") else "python3"


def found_test_files(repo):
    """(test files under the repository as posix paths relative to it, {skipped folder path: [the folder name that skips it, test files under it]}).
    git's own list is used when there is one (tracked files plus untracked files that are not ignored), so ignored folders are
    left out and only GIT_SKIP_DIRS are skipped by name; otherwise the tree is walked and SKIP_DIRS are skipped by name.
    What a folder name leaves out is counted per folder (the outermost skipped one), never dropped without a trace."""
    try:
        listed = subprocess.run(["git", "-C", str(repo), "-c", "core.quotepath=false", "ls-files", "--cached", "--others", "--exclude-standard"],
                                capture_output=True, text=True, encoding="utf-8", errors="replace")
    except OSError:                                        # git is not installed — the same as "not a git repository": walk the tree
        listed = None
    if listed is not None and listed.returncode == 0:
        names, skip = [line for line in listed.stdout.splitlines() if line], GIT_SKIP_DIRS
    else:
        names, skip = [p.relative_to(repo).as_posix() for p in repo.rglob("*.py")], SKIP_DIRS
    out, left_out = [], {}
    for name in names:
        parts = name.split("/")
        if not is_test_name(name) or not (repo / name).is_file():
            continue
        hit = next((i for i, part in enumerate(parts[:-1]) if part in skip), None)
        if hit is None:
            out.append(name)
        else:
            left_out.setdefault("/".join(parts[:hit + 1]) + "/", [parts[hit], 0])[1] += 1
    return sorted(out), left_out


def test_files(repo):
    """The test files of found_test_files alone"""
    return found_test_files(repo)[0]


def uses_pytest_config(repo):
    """The file that shows the repository is set up for pytest, or None"""
    for name, needle in PYTEST_CONFIG:
        path = repo / name
        if not path.is_file():
            continue
        if needle is None:
            return name
        try:
            if needle in path.read_text(encoding="utf-8", errors="replace"):
                return name
        except OSError:
            continue
    return None


TESTCASE_CLASS = re.compile(r"^\s*class\s+\w+\s*\([^)]*TestCase\b", re.M)      # `class T(unittest.TestCase)` · `class T(TestCase)` · `class T(Base, IsolatedAsyncioTestCase)`
TOP_TEST_FUNCTION = re.compile(r"^(?:async\s+)?def test_\w*\(", re.M)
ANY_TEST_FUNCTION = re.compile(r"^\s*(?:async\s+)?def test_\w*\(", re.M)
MODULE_EXIT = re.compile(r"^(?:raise SystemExit|sys\.exit)\(", re.M)             # at the start of a line: outside any function, runs on import
MAIN_BLOCK = re.compile(r"^if __name__ == [\"']__main__[\"']:", re.M)


def runner_for(repo, rel, repo_pytest):
    """(runner, reason) for one test file — runner is `pytest`, `unittest`, `script`, or None when it cannot be told.
    The decision order — the first rule that fits:
    1. the file imports pytest → pytest
    2. the file defines a class deriving from TestCase (`unittest.TestCase` or an imported `TestCase`) → unittest; pytest when the
       repository is set up for pytest (pytest runs such classes). An import of unittest alone decides nothing: with
       `from unittest import mock` and plain test_ functions, `python -m unittest` would run 0 tests
    3. the file defines top-level test_ functions → pytest
    4. the file exits by itself at module level (`sys.exit(` / `raise SystemExit(` at the start of a line) and has no test_ function →
       script, also in a repository set up for pytest: importing it during collection would hit the exit
    5. the repository is set up for pytest → pytest
    6. the file has a `__main__` block → script
    Otherwise None, with the reason"""
    try:
        text = (repo / rel).read_text(encoding="utf-8", errors="replace")
    except OSError as error:
        return None, "cannot be read (%s)" % error
    if re.search(r"^\s*(?:import pytest|from pytest\b)", text, re.M):
        return "pytest", "the file imports pytest"
    if TESTCASE_CLASS.search(text):
        if repo_pytest:
            return "pytest", "the repository is set up for pytest (%s)" % repo_pytest
        return "unittest", "the file defines a TestCase class"
    if TOP_TEST_FUNCTION.search(text):
        return "pytest", "the file defines top-level test_ functions"
    if MODULE_EXIT.search(text) and not ANY_TEST_FUNCTION.search(text):
        return "script", "the file exits by itself at module level, so it runs as a script"
    if repo_pytest:
        return "pytest", "the repository is set up for pytest (%s)" % repo_pytest
    if MAIN_BLOCK.search(text):
        return "script", "the file runs as a script"
    return None, "no pytest import, no TestCase class, no test_ function, and not a script"


def source_folder(repo):
    """`src` when the repository keeps its package under `src/` — else None.
    None too when `src/__init__.py` exists: the code is then imported as `src.x`, and `src` must not be put on the path.
    The test commands then start with `PYTHONPATH={root}/src` (see impact.split_env): without it an installed copy of the
    package, or an editable install pointing at the original folder, is imported instead of the code under test, and
    mutants in the throw-away copies are never executed. Only a `pytest` entry goes without the word, and only when pytest
    is already told the path (pytest_path_told) — that setting does nothing for a `script` or `unittest` entry."""
    src = repo / "src"
    if not src.is_dir() or not (any(src.glob("*/__init__.py")) or any(src.glob("*.py"))):
        return None
    if (src / "__init__.py").is_file():
        return None                                        # `src` is itself the package (imported as `src.x` from the repository root) — with src on the path `src.x` would not be found there
    return "src"


def pytest_path_told(repo):
    """Does the configuration already tell pytest which folders to put on the path (`pythonpath = …`) — read by pytest alone"""
    for name in ("pyproject.toml", "pytest.ini", "setup.cfg", "tox.ini"):
        try:
            if re.search(r"^\s*(?:ini_options\.)?pythonpath\s*=", (repo / name).read_text(encoding="utf-8", errors="replace"), re.M):
                return True
        except OSError:
            continue
    return False


SHELL_SPECIAL = re.compile(r"""[$`"'%!&();|<>^*?\[\]{}\t\n]""")
HAND_WRITTEN = "the path has a character the shell treats specially — write this entry by hand"


def command_for(runner, rel, python, source=None):
    """The command that runs one test file, or None when the path has a character the shell treats specially
    (any of $ ` " ' % ! & ( ) ; | < > ^ * ? [ ] { }, a tab or a newline): no quoting is right for both cmd and sh, so such an entry is
    left to be written by hand. A path with only a space is wrapped in double quotes, which both shells understand."""
    if SHELL_SPECIAL.search(rel):
        return None
    quoted = '"%s"' % rel if " " in rel else rel
    prefix = "PYTHONPATH={root}/%s " % source if source else ""
    if runner == "pytest":
        return "%s%s -m pytest %s -q" % (prefix, python, quoted)
    if runner == "unittest":
        return "%s%s -m unittest %s" % (prefix, python, quoted)
    return "%s%s %s" % (prefix, python, quoted)


def entry_name(rel, taken):
    """A short unique name for the list — the file stem, then with its folders when the stem is already used, then with a number
    when that is used too (`b.test_x_test.py` and `b/test_x.py` both come to `b.test_x`). Unique in every case"""
    stem = Path(rel).stem
    name = re.sub(r"^test_|_test$", "", stem) or stem
    if name in taken:
        name = base = rel[:-3].replace("/", ".")
        number = 1
        while name in taken:
            number += 1
            name = "%s.%d" % (base, number)
    taken.add(name)
    return name


def discover(repo):
    """([{name, command}], [{file, runner, reason}], [{file, reason}]) — the list, how each file was decided, the files left out"""
    python, repo_pytest, source, told = python_name(), uses_pytest_config(repo), source_folder(repo), pytest_path_told(repo)
    entries, decided, skipped, taken = [], [], [], set()
    found, left_out = found_test_files(repo)
    for rel in found:
        runner, reason = runner_for(repo, rel, repo_pytest)
        if runner is None:
            skipped.append({"file": rel, "reason": reason})
            continue
        # `pythonpath = …` in the configuration is read by pytest alone — a script or unittest entry still needs the PYTHONPATH word
        command = command_for(runner, rel, python, None if runner == "pytest" and told else source)
        if command is None:
            skipped.append({"file": rel, "reason": HAND_WRITTEN})
            continue
        entries.append({"name": entry_name(rel, taken), "command": command})
        decided.append({"file": rel, "runner": runner, "reason": reason})
    for folder, (name, count) in sorted(left_out.items()):
        skipped.append({"file": folder, "reason": LEFT_OUT % (count, name)})
    return entries, decided, skipped


def add_ignore_lines(repo):
    """Append the state-file lines missing from .gitignore — returns the lines added (None when the file cannot be read or written:
    a read-only .gitignore must not stop the setup after tests.json was written).
    Nothing is added when the whole folder is already ignored (`.oqa`, `.oqa/`, `.oqa/*`, `.oqa/**`, with or without a
    leading slash). A state file the user un-ignored on purpose with a `!` line is not added back. The file keeps its
    own line ending: a CRLF file gets CRLF lines."""
    path = repo / ".gitignore"
    try:
        with open(path, encoding="utf-8", errors="replace", newline="") as handle:     # newline="" — the line endings are read as they are
            text = handle.read()
    except FileNotFoundError:
        text = ""
    except OSError:
        return None
    rows = [line.strip() for line in text.splitlines()]
    have = {row.lstrip("/") for row in rows if not row.startswith("!")}
    kept_by_user = {row[1:].lstrip("/") for row in rows if row.startswith("!")}
    if have & WHOLE_FOLDER:
        return []                                          # the whole folder is already ignored — nothing to add (the specs are then not committed; said in the output)
    missing = [line for line in IGNORE_LINES if line not in have and line not in kept_by_user]
    if missing:
        end = "\r\n" if "\r\n" in text else "\n"
        try:
            with open(path, "a", encoding="utf-8", newline="") as handle:
                handle.write(("" if not text or text.endswith("\n") else end) + "# OQA machine-local state" + end + end.join(missing) + end)
        except OSError:
            return None
    return missing


def setup(repo, force=False):
    """Do the setup and return what was done: {tests_file: written|kept|all skipped|none found, entries, decided, skipped, ignore_added, whole_folder_ignored, source_folder}.
    `all skipped`: test files were found but none could be listed (every one is a skipped row) — not the same as `none found`.
    source_folder: the folder some command puts on PYTHONPATH, None when no command carries the word"""
    repo = Path(repo).resolve()
    (repo / ".oqa" / "specs").mkdir(parents=True, exist_ok=True)
    entries, decided, skipped = discover(repo)
    tests_path = repo / ".oqa" / "tests.json"
    if tests_path.is_file() and not force:
        state = "kept"
    elif not entries:
        state = "all skipped" if skipped else "none found"
    else:
        tests_path.write_text(json.dumps(entries, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        state = "written"
    added = add_ignore_lines(repo)
    try:
        ignored = subprocess.run(["git", "-C", str(repo), "check-ignore", "-q", ".oqa/specs/x.md"], capture_output=True).returncode == 0
    except OSError:                                        # git is not installed — nothing is ignored by it
        ignored = False
    return {"tests_file": state, "entries": entries, "decided": decided, "skipped": skipped, "ignore_added": added, "whole_folder_ignored": ignored,
            "source_folder": source_folder(repo) if any(e["command"].startswith("PYTHONPATH=") for e in entries) else None}


def show(repo, done):
    print("OQA setup · %s" % repo)
    print("\n.oqa/specs/ ready")
    if done["tests_file"] == "written":
        print(".oqa/tests.json written — %d entries (one per test file)" % len(done["entries"]))
    elif done["tests_file"] == "kept":
        print(".oqa/tests.json already exists — kept as it is (--force writes it again). Found now: %d test files" % len(done["entries"]))
    elif done["tests_file"] == "all skipped":
        print(".oqa/tests.json NOT written — test files were found but none could be listed — see the skipped rows. Write the list by hand: [{\"name\": ..., \"command\": ...}]")
    else:
        print(".oqa/tests.json NOT written — no test file (test_*.py, *_test.py) was found. Write the list by hand: [{\"name\": ..., \"command\": ...}]")
    for row in done["decided"]:
        print("  %-9s %s  (%s)" % (row["runner"], row["file"], row["reason"]))
    for row in done["skipped"]:
        print("  skipped   %s  (%s)" % (row["file"], row["reason"]))
    if done.get("source_folder"):
        print("\nsrc layout: commands start with PYTHONPATH={root}/%s so the tests import this repository's code (and, for mutants, the copy's code) rather than an installed package" % done["source_folder"])
    if done["ignore_added"] is None:
        print("\n.gitignore could not be read or written — add these lines yourself: %s" % ", ".join(IGNORE_LINES))
    elif done["ignore_added"]:
        print("\n.gitignore: added %s" % ", ".join(done["ignore_added"]))
    if done["whole_folder_ignored"]:
        print("\nnote: git ignores .oqa/ as a whole here, so the specs and the test list will not be committed. Ignore only the state files if you want them in the repository.")
    print("\nnext: commit what this wrote (the test list and the ignore lines), then `impact.py record --repo <root>` — it runs every entry once and shows which pass")
    print("      (in the middle of a change — a test file added to the list just now — do neither: `report.py` runs the new entry itself)")


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    parser = argparse.ArgumentParser(description="Set a repository up for OQA")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--force", action="store_true", help="write .oqa/tests.json again even when it exists")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    repo = Path(args.repo).resolve()
    if not repo.is_dir():
        print("not a folder: %s" % repo)
        return 2
    done = setup(repo, args.force)
    print(json.dumps(done, ensure_ascii=False, indent=1)) if args.json else show(repo, done)
    return 0 if done["tests_file"] in ("written", "kept") else 1


if __name__ == "__main__":
    sys.exit(main())
