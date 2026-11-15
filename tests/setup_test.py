# -*- coding: utf-8 -*-
"""Tests of the first-use helpers: `init.py` (test list found from the test files), `--jobs auto`, and the time estimate
that can stop a mutation run before it starts.

Builds small git repositories and compares what the tools do with answers fixed in advance.
"""
import _harness  # noqa: F401,E402 — collects the temp folders in one place for this test and removes it at the end
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
sys.path.insert(0, str(TOOLS))
import init as oqa_init  # noqa: E402
import mutate  # noqa: E402
import report  # noqa: E402

failures = []


def check(name, condition, text=""):
    print(("  ok  " if condition else "  FAIL  ") + name)
    if not condition:
        failures.append(name)
        if text != "":
            print("        " + str(text)[:600].replace("\n", " / "))


def git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), "-c", "user.email=t@t", "-c", "user.name=t"] + list(args), capture_output=True, check=True)


def run_tool(name, *args):
    done = subprocess.run([sys.executable, str(TOOLS / name)] + [str(a) for a in args], capture_output=True, text=True, encoding="utf-8", errors="replace")
    return done.returncode, done.stdout + done.stderr


def new_repo(files):
    root = Path(tempfile.mkdtemp()) / "target"
    root.mkdir()
    git(root, "init", "-q")
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text, encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "first")
    return root


PY = oqa_init.python_name()
SCRIPT_TEST = ("import sys\nsys.path.insert(0, '.')\nfrom calc import grade\n"
               "bad = [s for s, a in ((59, 'refused'), (60, 'accepted'), (61, 'accepted')) if grade(s) != a]\n"
               "for s in bad:\n    print('  FAIL  ' + str(s))\n"
               "print('failures: %d' % len(bad))\n"
               "sys.exit(1 if bad else 0)\n")
CALC = "def grade(score):\n    return 'accepted' if score >= 60 else 'refused'\n"

# ───────── init.py ─────────
print("init.py — the test list is found from the test files, one entry per file")
repo = new_repo({
    "calc.py": CALC,
    "tests/test_alpha.py": "import pytest\n\ndef test_a():\n    assert True\n",
    "tests/test_beta.py": "import unittest\n\nclass T(unittest.TestCase):\n    def test_b(self):\n        self.assertTrue(True)\n",
    "tests/test_gamma.py": "def test_c():\n    assert True\n",
    "checks/calc_test.py": SCRIPT_TEST,
    "checks/odd_test.py": "VALUE = 1\n",
    "tests/helper.py": "def helper():\n    return 1\n",
    "node_modules/pkg/test_vendor.py": "def test_v():\n    assert True\n",
    "sub/test_alpha.py": "import pytest\n\ndef test_a2():\n    assert True\n",
})
code, out = run_tool("init.py", "--repo", repo)
entries = json.loads((repo / ".oqa" / "tests.json").read_text(encoding="utf-8")) if (repo / ".oqa" / "tests.json").is_file() else None
by_command = {e["command"]: e["name"] for e in entries or []}
check("exit code 0 · .oqa/specs/ is made · the list is written", code == 0 and (repo / ".oqa" / "specs").is_dir() and entries is not None, out)
check("a file that imports pytest runs with pytest", "%s -m pytest tests/test_alpha.py -q" % PY in by_command, by_command)
check("a file that imports unittest runs with unittest (the repository is not set up for pytest)", "%s -m unittest tests/test_beta.py" % PY in by_command, by_command)
check("top-level test_ functions without an import run with pytest", "%s -m pytest tests/test_gamma.py -q" % PY in by_command, by_command)
check("a *_test.py that exits by itself runs as a script", "%s checks/calc_test.py" % PY in by_command, by_command)
check("a file that fits no runner is not put in the list — it is shown as skipped with the reason",
      not any("odd_test" in c for c in by_command) and "skipped   checks/odd_test.py" in out and "not a script" in out, out)
check("a helper next to the tests and a vendored folder are not test entries", not any("helper" in c or "node_modules" in c for c in by_command), by_command)
check("exactly the five test files are listed (one entry each)", len(entries or []) == 5, entries)
names = [e["name"] for e in entries or []]
check("entry names are unique — the second test_alpha (in path order) gets its folders in the name", len(set(names)) == len(names) and "alpha" in names and "tests.test_alpha" in names, names)
ignore = (repo / ".gitignore").read_text(encoding="utf-8")
check(".gitignore gets the four state-file lines", all(line in ignore.splitlines() for line in oqa_init.IGNORE_LINES), ignore)
check("the output names the lines it added (and does not claim the file could not be read)",
      ".gitignore: added " + ", ".join(oqa_init.IGNORE_LINES) in out and "could not be read" not in out, out)
kept_by_default = new_repo({"calc.py": CALC, "checks/calc_test.py": SCRIPT_TEST})
(kept_by_default / ".oqa").mkdir()
(kept_by_default / ".oqa" / "tests.json").write_text('[{"name": "mine", "command": "echo mine"}]', encoding="utf-8")
state = oqa_init.setup(kept_by_default)
check("setup() called without force keeps an existing list too (the default is not to overwrite)",
      state["tests_file"] == "kept" and json.loads((kept_by_default / ".oqa" / "tests.json").read_text(encoding="utf-8"))[0]["name"] == "mine", state)

(repo / ".oqa" / "tests.json").write_text('[{"name": "mine", "command": "echo mine"}]', encoding="utf-8")
code, out = run_tool("init.py", "--repo", repo)
check("an existing list is kept (exit code 0 · says kept) and the ignore lines are not added twice (nothing is reported as added)",
      code == 0 and "kept as it is" in out and ".gitignore" not in out and json.loads((repo / ".oqa" / "tests.json").read_text(encoding="utf-8"))[0]["name"] == "mine"
      and (repo / ".gitignore").read_text(encoding="utf-8").count(".oqa/evidence.jsonl") == 1, out)
code, out = run_tool("init.py", "--repo", repo, "--force")
check("--force writes the list again", code == 0 and len(json.loads((repo / ".oqa" / "tests.json").read_text(encoding="utf-8"))) == 5, out)

(repo / "conftest.py").write_text("", encoding="utf-8")
code, out = run_tool("init.py", "--repo", repo, "--force", "--json")
decided = {row["file"]: row["runner"] for row in json.loads(out)["decided"]}
check("in a repository set up for pytest (conftest.py) a unittest file runs with pytest too", decided.get("tests/test_beta.py") == "pytest", decided)

empty = new_repo({"calc.py": CALC})
code, out = run_tool("init.py", "--repo", empty)
check("no test file found — exit code 1 · no list is written · it says so (not an empty list that looks like a setup)",
      code == 1 and not (empty / ".oqa" / "tests.json").exists() and "NOT written" in out, out)

whole = new_repo({"calc.py": CALC, "checks/calc_test.py": SCRIPT_TEST, ".gitignore": ".oqa/\n"})
code, out = run_tool("init.py", "--repo", whole)
check("when .oqa/ is ignored as a whole nothing is appended and the output says the specs will not be committed",
      (whole / ".gitignore").read_text(encoding="utf-8") == ".oqa/\n" and "will not be committed" in out, out)

plain = Path(tempfile.mkdtemp()) / "nogit"
(plain / "checks").mkdir(parents=True)
(plain / "checks" / "calc_test.py").write_text(SCRIPT_TEST, encoding="utf-8")
(plain / "node_modules" / "pkg").mkdir(parents=True)
(plain / "node_modules" / "pkg" / "test_vendor.py").write_text("def test_v():\n    assert True\n", encoding="utf-8")
code, out = run_tool("init.py", "--repo", plain, "--json")
check("outside git the tree is walked instead — the test file is found, a vendored folder is still left out",
      [e["command"] for e in json.loads(out)["entries"]] == ["%s checks/calc_test.py" % PY], out)
hidden = new_repo({"calc.py": CALC, "checks/calc_test.py": SCRIPT_TEST, ".gitignore": "scratch/\n"})
(hidden / "scratch").mkdir()
(hidden / "scratch" / "test_tmp.py").write_text("def test_t():\n    assert True\n", encoding="utf-8")
(hidden / "checks" / "new_test.py").write_text(SCRIPT_TEST, encoding="utf-8")
code, out = run_tool("init.py", "--repo", hidden, "--json")
found = sorted(row["file"] for row in json.loads(out)["decided"])
check("in git: a test file in an ignored folder is not listed · an untracked test file that is not ignored is listed", found == ["checks/calc_test.py", "checks/new_test.py"], found)
check("when .gitignore already ends with a newline, a comment line and then the ignore lines are appended directly after it (no blank line is added)",
      (hidden / ".gitignore").read_text(encoding="utf-8") == "scratch/\n# OQA machine-local state\n" + "\n".join(oqa_init.IGNORE_LINES) + "\n", (hidden / ".gitignore").read_text(encoding="utf-8"))
ragged = new_repo({"calc.py": CALC, "checks/calc_test.py": SCRIPT_TEST, ".gitignore": "scratch/"})
run_tool("init.py", "--repo", ragged)
check("a .gitignore without a final newline gets one first (the first added line is not glued to the last old line)",
      (ragged / ".gitignore").read_text(encoding="utf-8").startswith("scratch/\n# OQA machine-local state\n.oqa/evidence.jsonl\n"), (ragged / ".gitignore").read_text(encoding="utf-8"))
UNIT = "import unittest\n\nclass T(unittest.TestCase):\n    def test_b(self):\n        self.assertTrue(True)\n"
with_table = new_repo({"tests/test_beta.py": UNIT, "pyproject.toml": "[project]\nname = \"x\"\n\n[tool.pytest.ini_options]\naddopts = \"-q\"\n"})
without_table = new_repo({"tests/test_beta.py": UNIT, "pyproject.toml": "[project]\nname = \"x\"\n"})
got_runners = [json.loads(run_tool("init.py", "--repo", r, "--json")[1])["decided"][0]["runner"] for r in (with_table, without_table)]
check("pyproject.toml counts as pytest setup only when it has a [tool.pytest…] table: with → pytest · without → unittest", got_runners == ["pytest", "unittest"], got_runners)
code, out = run_tool("init.py", "--repo", whole, "--json")
check("whole folder ignored: the result says so and lists no added line (an empty list — not 'could not read')",
      json.loads(out)["whole_folder_ignored"] is True and json.loads(out)["ignore_added"] == [], out)
code, out = run_tool("init.py", "--repo", repo / "no_such_folder")
check("--repo that is not a folder — exit code 2 · it says so", code == 2 and "not a folder" in out, (code, out))
done = subprocess.run([sys.executable, str(TOOLS / "init.py"), "--force"], capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(hidden))
check("without --repo the current folder is set up (its tests.json is written there)", done.returncode == 0 and (hidden / ".oqa" / "tests.json").is_file() and str(hidden.resolve()) in done.stdout, done.stdout)

# ───────── init.py — edge cases ─────────
print("init.py — edge cases")
flat = new_repo({"src/__init__.py": "", "src/calc.py": CALC, "checks/calc_test.py": SCRIPT_TEST})
code, out = run_tool("init.py", "--repo", flat, "--json")
check("src/__init__.py exists (the code is imported as src.x) — src is not put on PYTHONPATH: source_folder is None and the command has no PYTHONPATH word",
      oqa_init.source_folder(flat) is None and json.loads(out)["source_folder"] is None and json.loads(out)["entries"][0]["command"] == "%s checks/calc_test.py" % PY, out)

_real_run = subprocess.run


def _no_git(args, *rest, **options):
    if isinstance(args, (list, tuple)) and args and args[0] == "git":
        raise FileNotFoundError(2, "No such file or directory", "git")
    return _real_run(args, *rest, **options)


try:
    subprocess.run = _no_git
    try:
        listed, state = oqa_init.test_files(plain), None
    except OSError as error:
        listed = error
    try:
        state = oqa_init.setup(plain)
    except OSError as error:
        state = error
finally:
    subprocess.run = _real_run
check("git is not installed (starting it raises FileNotFoundError) — test_files walks the tree instead of crashing", listed == ["checks/calc_test.py"], listed)
check("git is not installed — the whole setup still finishes: the entry is found and the folder is not reported as ignored",
      isinstance(state, dict) and [e["command"] for e in state["entries"]] == ["%s checks/calc_test.py" % PY] and state["whole_folder_ignored"] is False, state)


def ignore_case(content):
    """(lines add_ignore_lines reports as added, the bytes of .gitignore afterwards) for a .gitignore with these bytes"""
    folder = Path(tempfile.mkdtemp())
    (folder / ".gitignore").write_bytes(content)
    return oqa_init.add_ignore_lines(folder), (folder / ".gitignore").read_bytes()


for form in ("/.oqa/", "/.oqa", ".oqa", ".oqa/", ".oqa/*", ".oqa/**"):
    added, after = ignore_case(("scratch/\n%s\n" % form).encode())
    check(".gitignore line `%s` means the whole folder is already ignored — nothing is appended" % form, added == [] and after == ("scratch/\n%s\n" % form).encode(), (added, after))
added, after = ignore_case(b".oqa/specs\n.oqa/evidence.json\n")
check("a line that only resembles the folder (.oqa/specs · .oqa/evidence.json) is not taken as the whole folder — the four lines are appended", added == list(oqa_init.IGNORE_LINES), (added, after))
added, after = ignore_case(b"*.log\n!.oqa/evidence.jsonl\n!/.oqa/core.json\n")
check("a state file the user un-ignored with a `!` line (with or without a leading slash) is not appended — the other lines are",
      added == [".oqa/impact-map.json", ".oqa/baseline.json"] and b"\n.oqa/evidence.jsonl" not in after and b"\n.oqa/core.json" not in after and b"\n.oqa/impact-map.json\n" in after, (added, after))
state_lines = [line.encode() for line in oqa_init.IGNORE_LINES]
added, after = ignore_case(b"scratch/\r\nbuild/\r\n")
check("a CRLF .gitignore gets CRLF lines appended (no line ending of the other kind is mixed in)",
      after == b"scratch/\r\nbuild/\r\n# OQA machine-local state\r\n" + b"\r\n".join(state_lines) + b"\r\n", after)
added, after = ignore_case(b"scratch/\r\nbuild/")
check("a CRLF .gitignore without a final line ending gets a CRLF first",
      after == b"scratch/\r\nbuild/\r\n# OQA machine-local state\r\n" + b"\r\n".join(state_lines) + b"\r\n", after)
added, after = ignore_case(b"scratch/\n")
check("an LF .gitignore gets LF lines appended on every system (no carriage return appears)", after == b"scratch/\n# OQA machine-local state\n" + b"\n".join(state_lines) + b"\n", after)

PYTEST_FILE = "import pytest\n\ndef test_a():\n    assert True\n"
special = new_repo({"calc.py": CALC, "tests/test_a&b.py": PYTEST_FILE, "tests/test_$x.py": PYTEST_FILE, "tests/test_my it.py": PYTEST_FILE, "tests/test_plain.py": PYTEST_FILE})
code, out = run_tool("init.py", "--repo", special, "--json")
special_done = json.loads(out)
check("a test file whose path has a character the shell treats specially (& · $) is not put in the list — it is reported as skipped: the entry must be written by hand",
      sorted(row["file"] for row in special_done["skipped"]) == ["tests/test_$x.py", "tests/test_a&b.py"] and all("by hand" in row["reason"] for row in special_done["skipped"])
      and not any("&" in e["command"] or "$" in e["command"] for e in special_done["entries"]), out)
check("a path with only a space is still listed, wrapped in double quotes · an ordinary path is listed as before",
      sorted(e["command"] for e in special_done["entries"]) == sorted(['%s -m pytest "tests/test_my it.py" -q' % PY, "%s -m pytest tests/test_plain.py -q" % PY]), special_done["entries"])
not_refused = [ch for ch in "$`\"'%!&();|<>^\n" if oqa_init.command_for("script", "tests/a%sb_test.py" % ch, PY) is not None]
check("command_for gives no command for a path with any of $ ` \" ' % ! & ( ) ; | < > ^ or a newline", not_refused == [], not_refused)
check("command_for: a space alone is quoted · a plain path is not", (oqa_init.command_for("script", "a b_test.py", PY), oqa_init.command_for("unittest", "a_test.py", PY, "src"))
      == ('%s "a b_test.py"' % PY, "PYTHONPATH={root}/src %s -m unittest a_test.py" % PY))

# the list init wrote really runs: record the map with it
runs =new_repo({"calc.py": CALC, "checks/calc_test.py": SCRIPT_TEST})
run_tool("init.py", "--repo", runs)
git(runs, "add", "-A")
git(runs, "commit", "-qm", "oqa setup")
code, out = run_tool("impact.py", "record", "--repo", runs)
check("the list init wrote is usable as it is — impact.py record runs the entry and it passes", code == 0 and "pass" in out and (runs / ".oqa" / "impact-map.json").is_file(), out)

# ───────── src layout ─────────
print("src layout — the tests must import the code under test, in the repository and in every mutant copy")
import impact  # noqa: E402
SRC_TEST = ("import sys\nfrom pkg.calc import grade\n"
            "bad = [s for s, a in ((59, 'refused'), (60, 'accepted'), (61, 'accepted')) if grade(s) != a]\n"
            "for s in bad:\n    print('  FAIL  ' + str(s))\n"
            "print('failures: %d' % len(bad))\n"
            "sys.exit(1 if bad else 0)\n")
src = new_repo({"src/pkg/__init__.py": "", "src/pkg/calc.py": CALC, "checks/calc_test.py": SRC_TEST})
code, out = run_tool("init.py", "--repo", src)
src_entries = json.loads((src / ".oqa" / "tests.json").read_text(encoding="utf-8"))
check("init sees src/ and starts the command with PYTHONPATH={root}/src · says so",
      src_entries[0]["command"] == "PYTHONPATH={root}/src %s checks/calc_test.py" % PY and "src layout" in out, (src_entries, out))
command, env = impact.split_env("PYTHONPATH={root}/src A=1 python x.py B=2", src, {"PYTHONPATH": "keep", "A": "0"})
check("split_env: leading NAME=value words leave the command · {root} is the folder it runs in · PYTHONPATH goes in front of the old value · others are set · a later B=2 stays an argument",
      command == "python x.py B=2" and env["PYTHONPATH"] == str(src.resolve()) + "/src" + os.pathsep + "keep" and env["A"] == "1" and "B" not in env, (command, env))
given = {"K": "v"}
check("split_env: a command without such words is unchanged", impact.split_env("python x.py", src, given) == ("python x.py", {"K": "v"}))
command, env = impact.split_env("K=new python x.py LATER=1 tail", src, given)
check("split_env: the env handed in is not modified in place · a NAME=value word later in the command (even with words after it) stays in the command",
      given == {"K": "v"} and env["K"] == "new" and command == "python x.py LATER=1 tail" and "LATER" not in env, (given, command, env))
git(src, "add", "-A")
git(src, "commit", "-qm", "oqa setup")
code, out = run_tool("impact.py", "record", "--repo", src)
src_map = json.loads((src / ".oqa" / "impact-map.json").read_text(encoding="utf-8"))["tests"] if (src / ".oqa" / "impact-map.json").is_file() else {}
check("recording: the test passes and the map holds lines of src/pkg/calc.py (the PYTHONPATH word of the command does not replace the path entry the recorder itself needs)",
      code == 0 and "src/pkg/calc.py" in src_map.get("calc", {}), (out, {k: sorted(v) for k, v in src_map.items()}))
bare = new_repo({"src/pkg/__init__.py": "", "src/pkg/calc.py": CALC, "checks/calc_test.py": SRC_TEST})
(bare / ".oqa").mkdir()
(bare / ".oqa" / "tests.json").write_text(json.dumps([{"name": "calc", "command": "%s checks/calc_test.py" % PY}]), encoding="utf-8")
code, out = run_tool("impact.py", "record", "--repo", bare)
check("without the PYTHONPATH word the same test cannot import the package — it is reported as fail, not passed over", "fail" in out and "pass" not in out.split("map")[0], out)
check("recording with a failing test — exit code 1 · the map is still written · the output names the failing test and says its lines come from a failing run",
      code == 1 and (bare / ".oqa" / "impact-map.json").is_file() and "failed while recording: calc" in out and "failing run" in out, (code, out))

# ───────── impact.py — edge cases ─────────
print("impact.py — edge cases")
import contextlib  # noqa: E402
import io  # noqa: E402
import locale  # noqa: E402
for quoted_command in ('FOO="a b" python x.py', "FOO='a b' python x.py"):
    command, env = impact.split_env(quoted_command, src, {})
    check("split_env: a value that starts with a quote is not an environment word — the command is left exactly as it is: %s" % quoted_command,
          command == quoted_command and "FOO" not in env, (command, env))
command, env = impact.split_env('A=1 FOO="a b" python x.py', src, {})
check("split_env: words before a quoted value are still taken · the rest is left for the shell", command == 'FOO="a b" python x.py' and env.get("A") == "1" and "FOO" not in env, (command, env))
command, env = impact.split_env("PYTHONPATH= python x.py", src, {"PYTHONPATH": "keep"})
check("split_env: an empty PYTHONPATH word leaves the existing value unchanged (no leading empty entry, which would mean the current folder)",
      command == "python x.py" and env["PYTHONPATH"] == "keep", (command, env))
command, env = impact.split_env("PYTHONPATH= B= python x.py", src, {})
check("split_env: an empty PYTHONPATH word without an existing value adds no entry · another name with an empty value is set to empty",
      command == "python x.py" and env.get("PYTHONPATH", "") == "" and env.get("B") == "", (command, env))
command, env = impact.split_env("A={root} python {root}/x.py", src, {})
check("split_env: {root} is replaced only inside the leading words, not elsewhere in the command (the docstring says so)",
      command == "python {root}/x.py" and env["A"] == str(src.resolve()) and "only inside" in impact.split_env.__doc__, (command, env))

_real_encoding = locale.getpreferredencoding
WIDE = chr(0xD55C) + chr(0xAE00)                          # two characters that take two bytes each in cp949 and three in UTF-8
try:
    locale.getpreferredencoding = lambda *args: "cp949"
    stray = impact.decode(b"\xb0FAILED x\n")
    legacy = impact.decode((WIDE + " ok FAILED\n").encode("cp949"))
finally:
    locale.getpreferredencoding = _real_encoding
check("decode: a stray byte before an ASCII letter does not swallow the letter in a double-byte code page — the failure word FAILED is still there",
      "FAILED x" in stray, repr(stray))
check("decode: output really written in the legacy encoding is still read in it (no ASCII character is lost, so the legacy text is kept)", legacy == WIDE + " ok FAILED\n", ascii(legacy))
check("decode: valid UTF-8 is read as UTF-8", impact.decode((WIDE + " ok").encode("utf-8")) == WIDE + " ok")

sel = new_repo({"calc.py": CALC, "checks/calc_test.py": SCRIPT_TEST, ".oqa/tests.json": json.dumps([{"name": "calc", "command": "%s checks/calc_test.py" % PY}]), ".gitignore": ".oqa/impact-map.json\n"})
run_tool("impact.py", "record", "--repo", sel)
(sel / "calc.py").write_text(CALC.replace("score >= 60", "score >= 60 and score < 1000"), encoding="utf-8")
code, plain_out = run_tool("impact.py", "select", "--repo", sel)
git(sel, "config", "color.ui", "always")
code, out = run_tool("impact.py", "select", "--repo", sel)
git(sel, "config", "--unset", "color.ui")
check("select with git set to always colour its output — the changed line is still seen and its test selected (the diff is asked for without colour)",
      "selected tests 1 / total 1: calc" in plain_out and "selected tests 1 / total 1: calc" in out, out)
git_calls = []


def _watch_git(args, *rest, **options):
    git_calls.append(list(args))
    return _real_run(args, *rest, **options)


try:
    subprocess.run = _watch_git
    impact.changed_old_lines(sel, "working")
    modified_now = impact.modified_py(sel)
finally:
    subprocess.run = _real_run
diff_calls = [call for call in git_calls if "diff" in call]
check("every git diff call is made with --no-color --no-ext-diff --no-textconv (the listing of modified files too)",
      len(diff_calls) == 2 and all({"--no-color", "--no-ext-diff", "--no-textconv"} <= set(call) for call in diff_calls) and modified_now == ["calc.py"], diff_calls)
code, out = run_tool("impact.py", "select", "--repo", sel, "--range", "no_such_ref_xyz")
check("select when the diff cannot be obtained (unknown range) — exit code 2 (cannot judge, as for a missing map) · it names the git command that failed · no selection is printed",
      code == 2 and "cannot judge" in out and "git diff" in out and "no_such_ref_xyz" in out and "selected tests" not in out, (code, out))


def _git_fails_on(word):
    def run(args, *rest, **options):
        if isinstance(args, (list, tuple)) and args and args[0] == "git" and word in args:
            return subprocess.CompletedProcess(args, 128, "", "fatal: boom")
        return _real_run(args, *rest, **options)
    return run


def select_text(repo):
    buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(buffer):
            got = impact.select(repo, "working", False, mutate.DEFAULT_FAIL_PATTERN)
    except SystemExit as error:
        got = "SystemExit(%s)" % error
    return got, buffer.getvalue()


try:
    subprocess.run = _git_fails_on("ls-files")
    untracked_code, untracked_out = select_text(sel)
    subprocess.run = _git_fails_on("diff")
    modified_failed = impact.modified_py(sel)
finally:
    subprocess.run = _real_run
check("select when the list of untracked files cannot be obtained — code 2 · it names the git command (an unreadable list is not taken as \"no new file\")",
      untracked_code == 2 and "cannot judge" in untracked_out and "ls-files" in untracked_out and "boom" in untracked_out, (untracked_code, untracked_out))
check("modified_py when git fails — not an empty list (which would read as \"nothing modified\"): one entry that says the git command failed",
      len(modified_failed) == 1 and "failed" in modified_failed[0] and "git diff" in modified_failed[0], modified_failed)

map_path = sel / ".oqa" / "impact-map.json"
good_map = map_path.read_text(encoding="utf-8")
for damaged in ("{\"tests\": {\"calc\"", "[]", "{\"created\": \"x\"}"):
    map_path.write_text(damaged, encoding="utf-8")
    code, out = run_tool("impact.py", "select", "--repo", sel)
    check("select with a map file that exists but cannot be used (%s) — code 2 · it says the file is damaged and to record again, not that there is no map" % damaged[:12],
          code == 2 and "damaged" in out and "record" in out and "no impact map" not in out, (code, out))
check("read_map still gives None for a damaged map (the other tools treat it as no usable map)", impact.read_map(sel) is None)
os.remove(map_path)
code, out = run_tool("impact.py", "select", "--repo", sel)
check("select without a map file — code 2 · it says there is no impact map (not that it is damaged)", code == 2 and "no impact map" in out and "damaged" not in out, (code, out))
map_path.write_text(good_map, encoding="utf-8")
(src / "src" / "pkg" / "calc.py").write_text(CALC.replace("score >= 60", "score >= 60 and score < 10 ** 9"), encoding="utf-8")
result_path = src / "result.json"
code, out = run_tool("mutate.py", "--repo", src, "--files", "src/pkg/calc.py", "--test", src_entries[0]["command"], "--tests-file", src / ".oqa" / "tests.json",
                     "--map", src / ".oqa" / "impact-map.json", "--json", result_path)
src_result = json.loads(result_path.read_text(encoding="utf-8")) if result_path.is_file() else {}
verdicts = [m["verdict"] for m in src_result.get("mutants", [])]
check("mutants of src/pkg/calc.py are killed — the test in each copy imported the copy's code (an import of the original would leave every mutant alive)",
      src_result.get("baseline") == "pass" and verdicts.count("killed") >= 2 and "gap" not in verdicts, (verdicts, out))
# killed alone does not show the command really ran in the copies — a command that cannot start also "fails". A function no check looks at must come out as survived
(src / "src" / "pkg" / "calc.py").write_text(CALC + "\n\ndef unused(score):\n    return score + 5 if score > 90 else score\n", encoding="utf-8")
code, out = run_tool("mutate.py", "--repo", src, "--files", "src/pkg/calc.py", "--test", src_entries[0]["command"], "--tests-file", src / ".oqa" / "tests.json", "--json", result_path)
by_line = {}
for m in json.loads(result_path.read_text(encoding="utf-8")).get("mutants", []):
    by_line.setdefault(m["line"], set()).add(m["verdict"])
check("with the same command the mutants of a function no check looks at survive (so the test really ran and passed in the copy — with the kills above: the command fails in a copy only when the code is wrong)",
      by_line == {6: {"survived"}}, (by_line, out))
os.remove(result_path)
nopkg = new_repo({"src/readme.txt": "notes\n", "calc.py": CALC, "checks/calc_test.py": SCRIPT_TEST})
code, out = run_tool("init.py", "--repo", nopkg, "--json")
check("a src/ folder that holds no Python package is not a src layout — the command has no PYTHONPATH word",
      json.loads(out)["source_folder"] is None and json.loads(out)["entries"][0]["command"] == "%s checks/calc_test.py" % PY, out)
configured = new_repo({"src/pkg/__init__.py": "", "src/pkg/calc.py": CALC, "tests/test_calc.py": "from pkg.calc import grade\n\ndef test_grade():\n    assert grade(60) == 'accepted'\n",
                       "pyproject.toml": "[tool.pytest.ini_options]\npythonpath = [\"src\"]\n"})
code, out = run_tool("init.py", "--repo", configured)
check("when pytest is already told the path (pythonpath = ...) the command is left without the word",
      json.loads((configured / ".oqa" / "tests.json").read_text(encoding="utf-8"))[0]["command"] == "%s -m pytest tests/test_calc.py -q" % PY and "src layout" not in out, out)

# ───────── init.py — runner order · PYTHONPATH word per runner · folders left out · read-only .gitignore · names ─────────
print("init.py — how the runner is decided")


def init_json(root):
    """(exit code, what `init.py --force --json` printed, as a dictionary)"""
    code_, out_ = run_tool("init.py", "--repo", root, "--force", "--json")
    try:
        return code_, json.loads(out_)
    except ValueError:
        return code_, {"error": out_}


def runners(done_):
    return {row["file"]: row["runner"] for row in done_.get("decided", [])}


MOCK_FUNC = "from unittest import mock\n\n\ndef test_x():\n    assert mock is not None\n"
IMPORTED_CASE = "from unittest import TestCase\n\n\nclass T(TestCase):\n    def test_b(self):\n        self.assertTrue(True)\n"
EXIT_RAISE = "failed = 0\nprint('failures: %d' % failed)\nraise SystemExit(1 if failed else 0)\n"
MAIN_BLOCK = "def run():\n    return 0\n\n\nif __name__ == '__main__':\n    raise SystemExit(run())\n"
NO_RUNNER = "no pytest import, no TestCase class, no test_ function, and not a script"
order = new_repo({"calc.py": CALC, "tests/test_mockfunc.py": MOCK_FUNC, "tests/test_case.py": UNIT, "tests/test_imported.py": IMPORTED_CASE,
                  "tests/test_mockonly.py": "import unittest.mock\nVALUE = 1\n", "checks/calc_test.py": SCRIPT_TEST, "checks/raise_test.py": EXIT_RAISE, "checks/main_test.py": MAIN_BLOCK})
code, done = init_json(order)
got = runners(done)
check("`from unittest import mock` with top-level test_ functions and no TestCase class is a pytest-style file: pytest (it used to get unittest, which runs 0 tests)",
      got.get("tests/test_mockfunc.py") == "pytest", got)
check("a class deriving from TestCase (unittest.TestCase · an imported TestCase) is what makes a file unittest", (got.get("tests/test_case.py"), got.get("tests/test_imported.py")) == ("unittest", "unittest"), got)
check("a file that only imports unittest (no TestCase class, no test_ function, not a script) fits no runner: skipped with the reason, not listed as unittest",
      "tests/test_mockonly.py" not in got and [row["reason"] for row in done.get("skipped", []) if row["file"] == "tests/test_mockonly.py"] == [NO_RUNNER], (got, done.get("skipped")))
check("without pytest setup the scripts (module-level sys.exit( · raise SystemExit( · a __main__ block) run as scripts",
      [got.get(f) for f in ("checks/calc_test.py", "checks/raise_test.py", "checks/main_test.py")] == ["script"] * 3, got)
(order / "conftest.py").write_text("", encoding="utf-8")
code, done = init_json(order)
got = runners(done)
check("in a repository set up for pytest a plain script that exits by itself at module level (sys.exit( · raise SystemExit() stays a script — collected by pytest, importing it would hit the exit (it used to get pytest)",
      (got.get("checks/calc_test.py"), got.get("checks/raise_test.py")) == ("script", "script"), got)
check("… while a TestCase file, a test_ function file and a file with only a __main__ block (its exit is inside the block) run with pytest there",
      [got.get(f) for f in ("tests/test_case.py", "tests/test_imported.py", "tests/test_mockfunc.py", "checks/main_test.py")] == ["pytest"] * 4, got)
check("the decision order is written in runner_for's docstring, and the module docstring names the TestCase rule too",
      "TestCase" in (oqa_init.runner_for.__doc__ or "") and "order" in (oqa_init.runner_for.__doc__ or "") and "TestCase" in (oqa_init.__doc__ or ""))

print("init.py — the PYTHONPATH word is dropped only for pytest entries")
PKG_PYTEST = "import pytest\nfrom pkg.calc import grade\n\n\ndef test_grade():\n    assert grade(60) == 'accepted'\n"
PKG_UNIT = "import unittest\nfrom pkg.calc import grade\n\n\nclass T(unittest.TestCase):\n    def test_b(self):\n        self.assertEqual(grade(60), 'accepted')\n"
told = new_repo({"src/pkg/__init__.py": "", "src/pkg/calc.py": CALC, "tests/test_calc.py": PKG_PYTEST, "tests/test_unit.py": PKG_UNIT, "checks/calc_test.py": SRC_TEST,
                 "tox.ini": "[tox]\nenvlist = py\n\n[testenv]\npythonpath = src\n"})
code, done = init_json(told)
commands = sorted(e["command"] for e in done.get("entries", []))
check("src layout with `pythonpath = …` in the configuration: only the pytest entry is left without the PYTHONPATH word — the script and the unittest entry keep it (it used to be dropped for every entry, so they imported an installed copy or nothing)",
      commands == sorted(["%s -m pytest tests/test_calc.py -q" % PY, "PYTHONPATH={root}/src %s -m unittest tests/test_unit.py" % PY, "PYTHONPATH={root}/src %s checks/calc_test.py" % PY]), commands)
code, out = run_tool("init.py", "--repo", told, "--force")
check("… the result names the source folder and the output says so (some commands carry the word)", done.get("source_folder") == "src" and "src layout" in out, (done.get("source_folder"), out))

print("init.py — test files left out by a folder name are reported")
tracked = new_repo({"calc.py": CALC, "tests/build/test_wheel.py": PYTEST_FILE, "dist/test_d.py": PYTEST_FILE, "node_modules/pkg/test_vendor.py": PYTEST_FILE,
                    "node_modules/pkg/deep/test_other.py": PYTEST_FILE, "tests/venv/test_v.py": PYTEST_FILE, "tests/test_ok.py": PYTEST_FILE})
code, done = init_json(tracked)
check("in git, test files under `build` and `dist` are listed (git's own listing already leaves ignored files out) — a tracked tests/build/test_wheel.py used to vanish without a trace",
      sorted(runners(done)) == ["dist/test_d.py", "tests/build/test_wheel.py", "tests/test_ok.py"], done)
LEFT_OUT = "%d test file(s) left out: the folder name `%s` is skipped (vendored code, environments, tool state) — write an entry by hand for a file that is a test of this repository"
check("test files under a folder that is still skipped by name (node_modules · venv) are reported as skipped rows — one per folder, with the count and the folder name",
      sorted((row["file"], row["reason"]) for row in done.get("skipped", [])) == [("node_modules/", LEFT_OUT % (2, "node_modules")), ("tests/venv/", LEFT_OUT % (1, "venv"))], done.get("skipped"))
code, out = run_tool("init.py", "--repo", tracked, "--force")
check("… and the rows are printed", "\n  skipped   node_modules/  (2 test file(s) left out: the folder name `node_modules` is skipped" in out, out)
loose = Path(tempfile.mkdtemp()) / "nogit_folders"
for name, text in (("checks/calc_test.py", SCRIPT_TEST), ("build/test_b.py", PYTEST_FILE), ("node_modules/pkg/test_vendor.py", PYTEST_FILE)):
    (loose / name).parent.mkdir(parents=True, exist_ok=True)
    (loose / name).write_text(text, encoding="utf-8")
code, done = init_json(loose)
check("outside git the tree is walked, so `build` is still skipped by name there — and reported the same way",
      [e["command"] for e in done.get("entries", [])] == ["%s checks/calc_test.py" % PY] and sorted(row["file"] for row in done.get("skipped", [])) == ["build/", "node_modules/"], done)

only_odd = new_repo({"calc.py": CALC, "checks/odd_test.py": "VALUE = 1\n"})
code, out = run_tool("init.py", "--repo", only_odd)
check("test files were found but every one was skipped: exit code 1 · no list written · its own message pointing at the skipped rows — not \"no test file … was found\"",
      code == 1 and not (only_odd / ".oqa" / "tests.json").exists() and "test files were found but none could be listed — see the skipped rows" in out
      and "no test file" not in out and "  skipped   checks/odd_test.py" in out, (code, out))
code, done = init_json(only_odd)
check("… its state is `all skipped` · a repository without any test file stays `none found`",
      code == 1 and done.get("tests_file") == "all skipped" and init_json(empty)[1].get("tests_file") == "none found", (code, done.get("tests_file")))

print("init.py — a .gitignore that cannot be written · more shell characters · unique names")
locked = new_repo({"calc.py": CALC, "checks/calc_test.py": SCRIPT_TEST, ".gitignore": "scratch/\n"})
_real_open = open


def _no_append(file, mode="r", *rest, **options):
    if "a" in mode and str(file).endswith(".gitignore"):
        raise PermissionError(13, "Permission denied", str(file))     # what appending to a read-only file raises
    return _real_open(file, mode, *rest, **options)


oqa_init.open = _no_append                                # the module's own name `open` — the built-in one is untouched
try:
    try:
        added = oqa_init.add_ignore_lines(locked)
    except OSError as error:
        added = "raised %r" % (error,)
    try:
        state = oqa_init.setup(locked)
    except OSError as error:
        state = "raised %r" % (error,)
finally:
    del oqa_init.open
shown = io.StringIO()
with contextlib.redirect_stdout(shown):
    if isinstance(state, dict):
        oqa_init.show(locked, state)
check("a read-only .gitignore (appending raises PermissionError): add_ignore_lines answers None instead of raising", added is None, added)
check("… the setup finishes (tests.json was already written) and the output says the file could not be read or written and lists the lines to add by hand",
      isinstance(state, dict) and state["tests_file"] == "written" and state["ignore_added"] is None and (locked / ".gitignore").read_text(encoding="utf-8") == "scratch/\n"
      and "\n.gitignore could not be read or written — add these lines yourself: " + ", ".join(oqa_init.IGNORE_LINES) in shown.getvalue(), (state, shown.getvalue()))

not_refused = [repr(ch) for ch in "*?[]{}\t" if oqa_init.command_for("script", "tests/a%sb_test.py" % ch, PY) is not None]
check("command_for gives no command for a path with any of * ? [ ] { } or a tab either (the shell expands or splits them)", not_refused == [], not_refused)
globby = new_repo({"calc.py": CALC, "tests/test_[x].py": PYTEST_FILE, "tests/test_{y}.py": PYTEST_FILE, "tests/test_plain.py": PYTEST_FILE})
code, done = init_json(globby)
check("a test file whose path has [ ] or { } is reported as skipped, to be written by hand — the plain one is listed",
      sorted(row["file"] for row in done.get("skipped", [])) == ["tests/test_[x].py", "tests/test_{y}.py"] and all("by hand" in row["reason"] for row in done.get("skipped", []))
      and [e["command"] for e in done.get("entries", [])] == ["%s -m pytest tests/test_plain.py -q" % PY], done)

taken_names = {"x", "b.test_x"}
first_name, second_name = oqa_init.entry_name("b/test_x.py", taken_names), oqa_init.entry_name("b/test_x.py", taken_names)
check("entry_name: when the fallback name (the path with dots) is taken too, a number is appended — every name is unique and is noted as taken",
      (first_name, second_name) == ("b.test_x.2", "b.test_x.3") and taken_names == {"x", "b.test_x", "b.test_x.2", "b.test_x.3"}, (first_name, second_name, taken_names))
twins = new_repo({"calc.py": CALC, "a/test_x.py": PYTEST_FILE, "b.test_x_test.py": PYTEST_FILE, "b/test_x.py": PYTEST_FILE})
code, done = init_json(twins)
check("… in a repository: a/test_x.py · b.test_x_test.py · b/test_x.py get three different names (the last two used to share `b.test_x`)",
      sorted(e["name"] for e in done.get("entries", [])) == ["b.test_x", "b.test_x.2", "x"], done.get("entries"))

# ───────── --jobs auto ─────────
print("--jobs auto")
auto = mutate.auto_jobs()
check("auto is between 1 and 8 and at most half the CPUs", 1 <= auto <= 8 and auto <= max(1, (os.cpu_count() or 2) // 2), auto)
_real_count = os.cpu_count
got_counts = []
try:
    for cpus in (1, 4, 6, 64, None):
        os.cpu_count = lambda cpus=cpus: cpus
        got_counts.append(mutate.auto_jobs())
finally:
    os.cpu_count = _real_count
check("auto by CPU count: 1 → 1 · 4 → 2 · 6 → 3 · 64 → 8 (the cap) · unknown → 1", got_counts == [1, 2, 3, 8, 1], got_counts)
check("jobs_arg: auto (any case) gives the auto number · a number is taken as it is", mutate.jobs_arg("AUTO") == auto and mutate.jobs_arg("3") == 3)
for bad in ("0", "-2"):
    try:
        mutate.jobs_arg(bad)
        check("jobs_arg refuses %s" % bad, False)
    except Exception as error:                            # noqa: BLE001
        check("jobs_arg refuses %s" % bad, "1 or more" in str(error), error)
try:
    mutate.jobs_arg("many")
    check("jobs_arg refuses a word that is not auto", False)
except ValueError:
    check("jobs_arg refuses a word that is not auto", True)

# ───────── time estimate ─────────
print("time estimate")
todo = [(None, "", "", {"selected": ["fast", "slow"]}), (None, "", "", {"selected": ["slow", "untimed"]}), (None, "", "", {"selected": []})]
low, high, untimed = mutate.estimate(todo, {"fast": 2.0, "slow": 10.0}, 2)
check("low = first timed test of each mutant, high = all timed tests, both divided by the jobs · an untimed test is counted, not taken as zero",
      (low, high, untimed) == ((2.0 + 10.0) / 2, (12.0 + 10.0) / 2, 1), (low, high, untimed))
check("minutes_text: seconds under 90 s, minutes under 90 min, hours above", (mutate.minutes_text(45), mutate.minutes_text(600), mutate.minutes_text(7200)) == ("45s", "10 min", "2.0 h"))

est = new_repo({"calc.py": CALC, "checks/calc_test.py": SCRIPT_TEST})
(est / "calc.py").write_text(CALC.replace(">= 60", ">= 70").replace("70", "60") + "\n\ndef bonus(score):\n    return score + 5 if score > 90 else score\n", encoding="utf-8")
table = est / "table.json"
command = "%s checks/calc_test.py" % PY
table.write_text(json.dumps([{"name": "calc", "command": command, "seconds": 900.0}]), encoding="utf-8")
result_path = est / "result.json"
code, out = run_tool("mutate.py", "--repo", est, "--files", "calc.py", "--test", command, "--tests-file", table, "--json", result_path, "--stop-over", "5")
stopped = json.loads(result_path.read_text(encoding="utf-8"))
check("low estimate over the limit — exit code 4 · the estimate is printed · it says stopped", code == 4 and "estimate for" in out and "stopped before running the mutants" in out, out)
check("stopped result: no mutant ran (mutants empty) · the reason is kept · the baseline it did run is kept",
      stopped.get("mutants") == [] and "estimate for" in stopped.get("stopped", "") and stopped.get("baseline") == "pass" and "counts" not in stopped, stopped)
code, out = run_tool("mutate.py", "--repo", est, "--files", "calc.py", "--test", command, "--tests-file", table, "--json", result_path, "--stop-over", "100000")
ran = json.loads(result_path.read_text(encoding="utf-8"))
check("under the limit — the estimate is printed and the mutants run as before", "estimate for" in out and "stopped" not in ran and len(ran.get("mutants", [])) > 0 and code in (0, 1), out)
table.write_text(json.dumps([{"name": "calc", "command": command}]), encoding="utf-8")
code, out = run_tool("mutate.py", "--repo", est, "--files", "calc.py", "--test", command, "--tests-file", table, "--json", result_path, "--stop-over", "0.0001")
unknown_time = json.loads(result_path.read_text(encoding="utf-8")) if result_path.is_file() else {}
check("no measured test time in the list and a tiny limit — the time measured in the copy is used: it stops on that estimate (exit code 4) · no mutant ran and the reason is kept",
      code == 4 and "estimate for" in unknown_time.get("stopped", "") and "cannot be estimated" not in out and unknown_time.get("mutants") == [], (code, out))
os.remove(table)
os.remove(result_path)

# the report: a stopped mutation run is "cannot judge", never a pass
print("report.py — stopped on the estimate")
rep = new_repo({"calc.py": CALC, "checks/calc_test.py": SCRIPT_TEST.replace("import sys\n", "import sys, time\ntime.sleep(1.2)\n")})
run_tool("init.py", "--repo", rep)
spec = rep / ".oqa" / "specs" / "demo.md"
spec.write_text("# Spec\n\n## Expected results\n- no numbered items here\n\n## Approval\n- 2026-01-01 · \"go\"\n\n## Results\n", encoding="utf-8")
git(rep, "add", "-A")
git(rep, "commit", "-qm", "setup")
run_tool("impact.py", "record", "--repo", rep)
(rep / "calc.py").write_text(CALC.replace("score >= 60", "score >= 60 and score < 1000"), encoding="utf-8")
got = report.collect(rep, spec, jobs=1, stop_over=0.0001)
verdict = report.judge(got)
check("collect with a tiny limit: the mutation part is unknown and says the mutants were not run and how to go ahead",
      "mutants were not run" in got["mutation"].get("unknown", "") and "--yes" in got["mutation"]["unknown"], got["mutation"])
check("the report cannot judge (code 2, the reason is in the unknown list — not all passed) · the Mutation Test evidence result is unknown",
      verdict[0] == 2 and any("mutants were not run" in reason for reason in verdict[2])
      and report.check_results(got).get("Mutation Test", ("", ""))[0] == "unknown", (verdict, report.check_results(got).get("Mutation Test")))
code, out = run_tool("report.py", "--repo", rep, "--spec", spec, "--no-write", "--stop-over", "0.0001")
check("report.py --stop-over: exit code is not 0 and the text says cannot judge", code != 0 and "cannot judge" in out, out)
code, out = run_tool("report.py", "--repo", rep, "--spec", spec, "--no-write", "--stop-over", "0.0001", "--yes")
check("report.py --yes runs the mutants whatever the estimate (mutants are counted)", "mutants were not run" not in out and "killed" in out, out)

# ───────── a change that adds a brand-new test file ─────────
# The map is recorded on the clean commit, before the new test file exists, so the map can neither select it nor say that a test passes through it.
# Listed in .oqa/tests.json it must be run and the flow must reach DONE; not listed, the tools must say so instead of leaving a bare gap
print("a change that adds a brand-new test file — plan · report · verdict")
EXTRA_TEST = ("# verifies: demo/E1\nimport sys\nsys.path.insert(0, '.')\nfrom calc import grade\n"
              "bad = [s for s, a in ((0, 'refused'), (59, 'refused'), (60, 'accepted'), (61, 'accepted'), (100, 'accepted')) if grade(s) != a]\n"
              "for s in bad:\n    print('  FAIL  ' + str(s))\n"
              "print('failures: %d' % len(bad))\n"
              "sys.exit(1 if bad else 0)\n")
NOT_LISTED = "checks/extra_test.py looks like a test file that is not listed in .oqa/tests.json — add an entry for it (or run `init.py --force`)"
fresh = new_repo({"calc.py": CALC, "checks/calc_test.py": SCRIPT_TEST, ".gitignore": "__pycache__/\n"})
run_tool("init.py", "--repo", fresh)
fresh_spec = fresh / ".oqa" / "specs" / "demo.md"
fresh_spec.write_text("# Spec\n\n## Expected results\n- E1 the boundary is 60\n\n## Finish line\n- Regression Test · Mutation Test · Requirements Traceability\n\n"
                      "## Approval\n- 2026-01-01 · \"go\"\n\n## Results\n", encoding="utf-8")
(fresh / ".oqa" / "current").write_text("demo.md\n", encoding="utf-8")
git(fresh, "add", "-A")
git(fresh, "commit", "-qm", "setup")
run_tool("impact.py", "record", "--repo", fresh)
(fresh / "calc.py").write_text(CALC.replace("score >= 60", "not score < 60"), encoding="utf-8")
(fresh / "checks" / "extra_test.py").write_text(EXTRA_TEST, encoding="utf-8")

# (a) the user did not add an entry for the new test file
code, plan_out = run_tool("core.py", "plan", "--repo", fresh)
check("not listed — core.py plan says in one line that the new file looks like a test file without an entry in .oqa/tests.json, and how to add one",
      code == 0 and len([line for line in plan_out.splitlines() if "checks/extra_test.py" in line and "not listed in .oqa/tests.json" in line and "init.py --force" in line]) == 1, plan_out)
code, out = run_tool("report.py", "--repo", fresh, "--spec", fresh_spec, "--jobs", "2")
written_report = out[out.find("### Verification Report"):]
check("not listed — the report does not leave a bare gap: it says that the file looks like a test file that is not listed and to add an entry (cannot judge, not a pass), and \"Gaps: none\"",
      code != 0 and "- unknown: " + NOT_LISTED in written_report and "gap checks/extra_test.py" not in written_report and "- Gaps: none" in written_report
      and "all passed" not in written_report, written_report)
code, out = run_tool("core.py", "verdict", "--repo", fresh)
check("not listed — the verdict is not DONE and carries the same message", code != 0 and "DONE" not in out.splitlines()[0] and NOT_LISTED in out, out)

# (b) the user adds the entry (init.py --force writes the list again) — after the map was recorded
code, out = run_tool("init.py", "--repo", fresh, "--force")
names = [entry["name"] for entry in json.loads((fresh / ".oqa" / "tests.json").read_text(encoding="utf-8"))]
code, plan_out = run_tool("core.py", "plan", "--repo", fresh)
check("listed — init.py --force adds the entry and core.py plan no longer has that line", names == ["calc", "extra"] and code == 0 and "not listed in .oqa/tests.json" not in plan_out, (names, plan_out))
got = report.collect(fresh, fresh_spec, jobs=2)
picture = got.get("impact", {})
check("listed — editing .oqa/tests.json after recording does not make the map stale, and the new test is run and selected next to the recorded one",
      picture.get("map_stale") is False and picture.get("results") == {"calc": "pass", "extra": "pass"} and picture.get("selected", {}).get("extra") == ["checks/extra_test.py"], picture)
rows = got.get("mutation", {}).get("mutants") or []
check("listed — the new test is among the tests of every mutant of the change (a test added after the map was recorded is run for every mutant) and every mutant is killed",
      rows and all("extra" in row.get("tests", []) for row in rows) and all(row.get("verdict") == "killed" for row in rows), [(row.get("line"), row.get("kind"), row.get("verdict"), row.get("tests")) for row in rows])
check("listed — the expected result the new file verifies passes, verified by the new test", [(i["id"], i["tests"], i["verdict"]) for i in got.get("spec", {}).get("items", [])] == [("E1", ["extra"], "pass")], got.get("spec"))
check("listed — the new test file is confirmed by its own passing run and the report is all passed",
      report.confirmed_gaps(got).get("checks/extra_test.py") == "new test file — its own run passed (extra)" and report.judge(got) == (0, [], []), (report.gap_states(got), report.judge(got)))
code, out = run_tool("report.py", "--repo", fresh, "--spec", fresh_spec, "--jobs", "2")
check("listed — report.py ends with exit code 0 (all passed)", code == 0 and "**Verdict: all passed**" in out, out[out.find("### Verification Report"):])
code, out = run_tool("core.py", "verdict", "--repo", fresh)
check("listed — the verdict is DONE", code == 0 and out.startswith("Core verdict: DONE"), out)

# a test-only change that adds a new listed test file: nothing to mutate, the new test runs, DONE
git(fresh, "checkout", "--", "calc.py")
run_tool("core.py", "plan", "--repo", fresh)
code, out = run_tool("report.py", "--repo", fresh, "--spec", fresh_spec, "--jobs", "2")
check("test-only change adding a new listed test file — the report is all passed (its own run confirms the file · nothing to mutate)", code == 0 and "**Verdict: all passed**" in out, out[out.find("### Verification Report"):])
code, out = run_tool("core.py", "verdict", "--repo", fresh)
check("test-only change adding a new listed test file — the verdict is DONE", code == 0 and out.startswith("Core verdict: DONE"), out)

# the same change plus one added `import os` in calc.py: mutate.py generates 0 mutants and names calc.py inert — report.py reads that as "nothing to
# mutate" (as if no Python source had changed), not as "generated 0", which no test could ever turn into a result
(fresh / "calc.py").write_text("import os\n" + CALC, encoding="utf-8")
run_tool("core.py", "plan", "--repo", fresh)
got = report.collect(fresh, fresh_spec, jobs=2)
check("a change whose only source edit is an added import — the mutation result is \"none\" naming the file and its inert list, with the baseline results kept",
      sorted(got.get("mutation", {})) == ["added_without_mutants", "baseline_results", "inert_files", "none"] and got["mutation"]["inert_files"] == ["calc.py"]
      and got["mutation"]["added_without_mutants"] == {"calc.py": [[1, 1]]} and report.confirmed_gaps(got).get("calc.py") == "added lines with nothing to mutate: 1"
      and got["mutation"]["none"].startswith("the changed Python source holds nothing to mutate (calc.py: ") and report.judge(got) == (0, [], []), (got.get("mutation"), report.judge(got)))
code, out = run_tool("report.py", "--repo", fresh, "--spec", fresh_spec, "--jobs", "2")
check("… and the report is all passed", code == 0 and "**Verdict: all passed**" in out, out[out.find("### Verification Report"):])
git(fresh, "checkout", "--", "calc.py")
run_tool("core.py", "plan", "--repo", fresh)

# ───────── an existing source file no recorded test covers, changed together with a new test for it ─────────
# util.py is committed but no test executes it, so the map holds no line of it. The change edits util.py and adds the test: the map can never say that
# a test passes through the changed line. What can say it is a killed mutant — a test that fails when the line is broken executes it and checks it
print("an uncovered source file changed together with a new test for it — plan · report · verdict")
UTIL = "# a small helper\ndef triple(x):\n    return x * 2\n"
UTIL_TEST = ("# verifies: demo/E1\n# verifies: demo/E2\nimport sys\nsys.path.insert(0, '.')\nfrom util import triple\n"
             "bad = [x for x, want in %s if triple(x) != want]\n"
             "for x in bad:\n    print('  FAIL  ' + str(x))\n"
             "print('failures: %%d' %% len(bad))\n"
             "sys.exit(1 if bad else 0)\n")
SPEC_HEAD = "# Spec\n\n## Expected results\n- E1 triple multiplies by three\n%s\n## Finish line\n- Regression Test · Mutation Test · Requirements Traceability\n\n## Approval\n- 2026-01-01 · \"go\"\n\n## Results\n"
lone = new_repo({"calc.py": CALC, "util.py": UTIL, "checks/calc_test.py": SCRIPT_TEST, "checks/odd_test.py": "# verifies: demo/E2\nVALUE = 1\n", ".gitignore": "__pycache__/\n"})
run_tool("init.py", "--repo", lone)
lone_spec = lone / ".oqa" / "specs" / "demo.md"
lone_spec.write_text(SPEC_HEAD % "", encoding="utf-8")
(lone / ".oqa" / "current").write_text("demo.md\n", encoding="utf-8")
git(lone, "add", "-A")
git(lone, "commit", "-qm", "setup")
run_tool("impact.py", "record", "--repo", lone)
recorded = json.loads((lone / ".oqa" / "impact-map.json").read_text(encoding="utf-8"))
check("setup — no recorded test executes util.py", not [name for name, files in recorded["tests"].items() if "util.py" in files], recorded["tests"])


def lone_flow(util_text, cases):
    """plan → report → verdict for that state of util.py and that list of cases in the new test: (report exit code, report text, verdict exit code, verdict text)"""
    (lone / "util.py").write_text(util_text, encoding="utf-8")
    (lone / "tests").mkdir(exist_ok=True)
    (lone / "tests" / "test_util.py").write_text(UTIL_TEST % cases, encoding="utf-8")
    run_tool("init.py", "--repo", lone, "--force")
    run_tool("core.py", "plan", "--repo", lone)
    code, out = run_tool("report.py", "--repo", lone, "--spec", lone_spec, "--jobs", "2")
    return (code, out[out.find("### Verification Report"):]) + run_tool("core.py", "verdict", "--repo", lone)


# (1) a good new test: both mutants of the changed line are killed
code, written_report, verdict_code, verdict_out = lone_flow(UTIL.replace("x * 2", "x * 3"), "((0, 0), (2, 6))")
check("good new test — every mutant of the changed line is killed and the report is all passed (it used to keep \"gap util.py\" although nothing survived)",
      code == 0 and "**Verdict: all passed**" in written_report and "- Gaps: none" in written_report and "killed 2 · survived 0 · crashed 0 · gap 0" in written_report, written_report)
check("good new test — the report says what settled the changed lines: confirmed by other evidence, all 2 mutants on them killed",
      "- Changed lines no recorded test passed through — confirmed by other evidence: util.py (all 2 mutant(s) on them killed)" in written_report, written_report)
check("good new test — the verdict is DONE (it was never DONE before)", verdict_code == 0 and verdict_out.startswith("Core verdict: DONE"), verdict_out)
code, out = run_tool("impact.py", "select", "--repo", lone)
select_lines = out.splitlines()
check("impact.py select on its own — the new listed test file is not printed as a \"gap … new file\": the line says it is a new test file listed in .oqa/tests.json that the report runs and settles",
      not [s for s in select_lines if s.startswith("  gap tests/test_util.py")]
      and "  new test file tests/test_util.py — listed in .oqa/tests.json; it is in no map (recorded before the file existed), so report.py runs it and settles it by its own result" in select_lines, out)
check("… the gap of util.py is still printed as a gap, and the exit code is still 1 (select did not run the new test)", code == 1 and [s for s in select_lines if s.startswith("  gap util.py — no test passed through changed lines")], (code, out))

# an expected result tagged in the listed new test and in a committed file that no entry of the test list runs (checks/odd_test.py fits no runner)
lone_spec.write_text(SPEC_HEAD % "- E2 triple of zero is zero\n", encoding="utf-8")
code, out = run_tool("report.py", "--repo", lone, "--spec", lone_spec, "--jobs", "2")
written_report = out[out.find("### Verification Report"):]
check("an expected result tagged in two files, one of them run by no listed test — its row shows the test that ran and the file that was not run, with \"not run\" (it used to read \"pass\")",
      "| E2 triple of zero is zero | util · not run: checks/odd_test.py | not run |" in written_report and "| E1 triple multiplies by three | util | pass |" in written_report, written_report)
check("… the report is cannot judge and names the file and what to do", code == 2 and "**Verdict: cannot judge**" in written_report
      and "- unknown: expected result \"triple of zero is zero\": not run — tagged file(s) no test listed in .oqa/tests.json runs: checks/odd_test.py (list them, or remove the tag)" in written_report, written_report)
verdict_code, verdict_out = run_tool("core.py", "verdict", "--repo", lone)
check("… and the verdict is not DONE", verdict_code != 0 and "DONE" not in verdict_out.splitlines()[0], verdict_out)
lone_spec.write_text(SPEC_HEAD % "", encoding="utf-8")

# (2) a weak new test (only triple(0) == 0): the mutant `x * 4` gets past it
code, written_report, verdict_code, verdict_out = lone_flow(UTIL.replace("x * 2", "x * 3"), "((0, 0),)")
check("weak new test — a mutant survives: incomplete, the survivor is named with its line, and the gap is not lifted into \"confirmed\"",
      code == 1 and "**Verdict: incomplete 1**" in written_report and "- survived mutants 1" in written_report and "  - survived util.py:3 number +1 — mutated line: `return x * 4`" in written_report
      and "- Changed lines not confirmed — mutants on them survived: util.py (1 survived)" in written_report and "confirmed by other evidence: util.py" not in written_report, written_report)
check("weak new test — the verdict is not DONE", verdict_code != 0 and "DONE" not in verdict_out.splitlines()[0], verdict_out)

# (3) the change of util.py is only a comment: no mutant can be generated, so nothing shows that a test executes the changed line
code, written_report, verdict_code, verdict_out = lone_flow(UTIL.replace("# a small helper", "# a small helper, used by nobody yet"), "((0, 0), (2, 4))")
check("comment-only change of the uncovered file — zero mutants: the gap is not lifted (\"gap util.py\") and the mutation part says generated 0",
      code == 1 and "- gap util.py" in written_report and "generated 0" in written_report and "confirmed by other evidence: util.py" not in written_report, written_report)
check("comment-only change of the uncovered file — the verdict is not DONE", verdict_code != 0 and "DONE" not in verdict_out.splitlines()[0], verdict_out)

# ───────── a new file under a tests folder that is not named like a test ─────────
print("a new support file under a tests folder — mutated like a new source file")
HELPERS = "def expected(score):\n    return 'accepted' if score >= 60 else 'refused'\n"
GRADE_TEST = ("# verifies: demo/E1\nimport sys\nsys.path.insert(0, '.')\nsys.path.insert(0, 'tests')\nimport helpers\nfrom calc import grade\n"
              "bad = [s for s in %s if grade(s) != helpers.expected(s)]\n"
              "for s in bad:\n    print('  FAIL  ' + str(s))\n"
              "print('failures: %%d' %% len(bad))\n"
              "sys.exit(1 if bad else 0)\n")
helped = new_repo({"calc.py": CALC, "checks/calc_test.py": SCRIPT_TEST, ".gitignore": "__pycache__/\n"})
run_tool("init.py", "--repo", helped)
helped_spec = helped / ".oqa" / "specs" / "demo.md"
helped_spec.write_text((SPEC_HEAD % "").replace("triple multiplies by three", "the boundary is 60"), encoding="utf-8")
(helped / ".oqa" / "current").write_text("demo.md\n", encoding="utf-8")
git(helped, "add", "-A")
git(helped, "commit", "-qm", "setup")
run_tool("impact.py", "record", "--repo", helped)
(helped / "calc.py").write_text(CALC.replace("score >= 60", "not score < 60"), encoding="utf-8")
(helped / "tests").mkdir()
(helped / "tests" / "helpers.py").write_text(HELPERS, encoding="utf-8")
(helped / "tests" / "test_grade.py").write_text(GRADE_TEST % "(0, 59, 60, 61, 100)", encoding="utf-8")
run_tool("init.py", "--repo", helped, "--force")
run_tool("core.py", "plan", "--repo", helped)
got = report.collect(helped, helped_spec, jobs=2)
rows = [row for row in got.get("mutation", {}).get("mutants") or [] if row.get("file") == "tests/helpers.py"]
check("the new tests/helpers.py is mutated, and every mutant in it is killed by the test that uses it", rows and all(row.get("verdict") == "killed" for row in rows),
      [(row.get("file"), row.get("line"), row.get("kind"), row.get("verdict")) for row in got.get("mutation", {}).get("mutants") or []])
check("… it is confirmed with a basis that says what it is (it used to stay a bare \"gap tests/helpers.py\" for ever) and the report is all passed",
      report.confirmed_gaps(got).get("tests/helpers.py") == "new support file for tests (not named like a test) — all %d mutant(s) of that file killed" % len(rows)
      and report.judge(got) == (0, [], []), (report.gap_states(got), report.judge(got)))
code, out = run_tool("report.py", "--repo", helped, "--spec", helped_spec, "--jobs", "2")
verdict_code, verdict_out = run_tool("core.py", "verdict", "--repo", helped)
check("… report.py ends all passed and the verdict is DONE", code == 0 and "**Verdict: all passed**" in out and verdict_code == 0 and verdict_out.startswith("Core verdict: DONE"), (out[out.find("### Verification Report"):], verdict_out))
(helped / "tests" / "test_grade.py").write_text(GRADE_TEST % "(0, 100)", encoding="utf-8")       # the boundary of the helper is no longer checked
code, out = run_tool("report.py", "--repo", helped, "--spec", helped_spec, "--jobs", "2", "--no-write")
check("… with a test that does not check the helper's boundary, the survivors in tests/helpers.py are listed and the report is incomplete",
      code == 1 and "  - survived tests/helpers.py:2" in out and "- Added lines not confirmed — mutants on them survived: tests/helpers.py" in out, out[out.find("### Verification Report"):])

print("\nfailures: %d" % len(failures))
sys.exit(1 if failures else 0)
