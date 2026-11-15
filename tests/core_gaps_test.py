# -*- coding: utf-8 -*-
"""OQA Core (scripts/core.py) — edge decisions of the module: the module's own import path, the edges of the
thresholds, staged · deleted · unreadable files in the list of changes, the verdict's "cannot judge" and "plan again" answers, and what the
command line prints and returns.

Builds small throw-away git repositories and compares what core.py answers with values fixed in advance.
`python tests/core_gaps_test.py`
"""
import _harness  # noqa: F401,E402 — collects the temp folders in one place for this test and removes it at the end (first)
import contextlib
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import core  # noqa: E402
import evidence  # noqa: E402
import findings  # noqa: E402
import impact  # noqa: E402

failures = []


def check(name, condition, text=""):
    """condition may be a function — an exception while working it out is a FAIL line of this check, not a crash of the whole test"""
    if callable(condition):
        try:
            condition = bool(condition())
        except Exception as error:                       # noqa: BLE001
            condition, text = False, "raised %s: %s · %s" % (type(error).__name__, error, text)
    print(("  ok  " if condition else "  FAIL  ") + name)
    if not condition:
        failures.append(name)
        if text != "":
            print("        " + str(text)[:900].replace("\n", " / "))


def attempt(func, *args, **kwargs):
    """The value of the call, or a text that says what it raised (so that the checks after it fail by value)"""
    try:
        return func(*args, **kwargs)
    except Exception as error:                           # noqa: BLE001
        return "raised %s: %s" % (type(error).__name__, error)


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", "-c", "core.autocrlf=false"] + list(args),
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


BASE_SPEC = "# Spec\n\n## What\nTidy the screen\n\n## Expected results\n- E1 the screen opens\n\n## Approval\n- 2099-01-01 · \"yes, go ahead\"\n\n## Results\n"
GATE_SPEC = BASE_SPEC + "\n## Independent review gate\n- Independent review gate: on (user 2099-01-01 \"use the review gate\")\n"
SPEC_FILE = ".oqa/specs/2099-01-01_a.md"
ORIGIN_FILES = {"README.md": "# Guide\n", "ui/a.html": "<p>a</p>\n", "tools/score.py": "def tally(x):\n    return x + 1\n",
                "hooks/scripts/h.py": "X = 1\n", ".gitignore": "__pycache__/\n"}
origin = None


def write(repo, path, text):
    (repo / path).parent.mkdir(parents=True, exist_ok=True)
    (repo / path).write_text(text, encoding="utf-8", newline="\n")


def make_repo(spec_text=BASE_SPEC, with_map=True):
    """A copy of one committed repository (README · ui · judging tool · hook). spec_text None → no .oqa/specs at all"""
    global origin
    if origin is None:
        origin = Path(tempfile.mkdtemp()) / "origin"
        for path, text in ORIGIN_FILES.items():
            write(origin, path, text)
        git(origin, "init", "-q")
        git(origin, "add", "-A")
        git(origin, "commit", "-q", "-m", "first")
    repo = Path(tempfile.mkdtemp()) / "proj"
    shutil.copytree(origin, repo)
    (repo / ".oqa").mkdir()
    if spec_text is not None:
        write(repo, ".oqa/current", "2099-01-01_a.md\n")
        write(repo, SPEC_FILE, spec_text)
    if with_map:
        write(repo, ".oqa/impact-map.json", '{"HEAD": "x", "tests": {}}')      # impact.py takes only an object with a "tests" table as a map
    return repo


def record(repo, kind, result, fingerprint_value):
    return evidence.record(repo, kind, result, "recorded by the test", fingerprint_value, "core_gaps_test")


def names(result):
    return [c["name"] for c in result["checks"]]


def reason_of(result, name):
    return next(c["reason"] for c in result["checks"] if c["name"] == name)


def run_main(argv):
    """(what core.main returned, what it printed)"""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = attempt(core.main, argv)
    return code, out.getvalue()


def command_line(*args):
    done = subprocess.run([sys.executable, str(SCRIPTS / "core.py")] + [str(a) for a in args], capture_output=True, text=True, encoding="utf-8", errors="replace")
    return done.returncode, done.stdout


# ───────── the module finds its sibling modules by itself ─────────
print("import path — core.py puts its own folder first on the module path")
LOADER = ("import importlib.util\nimport sys\n"
          "if sys.argv[2] == 'already':\n    sys.path.insert(0, sys.argv[3])\n"
          "found = importlib.util.spec_from_file_location('core_under_test', sys.argv[1])\n"
          "module = importlib.util.module_from_spec(found)\n"
          "found.loader.exec_module(module)\n"
          "print('SPEC=' + module.spec.__file__)\n"
          "print('COUNT=%d' % sys.path.count(sys.argv[3]))\n"
          "print('FIRST=' + sys.path[0])\n")
decoy = Path(tempfile.mkdtemp()) / "decoy"
write(decoy, "loader.py", LOADER)
write(decoy, "spec.py", "DECOY = True\n")                 # a module of the same name in the folder of the starting script (sys.path[0])
clean_env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONSTARTUP")}


def load_core(mode):
    done = subprocess.run([sys.executable, str(decoy / "loader.py"), str(SCRIPTS / "core.py"), mode, str(SCRIPTS)],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", env=clean_env, cwd=str(decoy))
    return done.returncode, dict(line.split("=", 1) for line in done.stdout.splitlines() if "=" in line), done.stdout + done.stderr


code, got, text = load_core("absent")
count_absent = got.get("COUNT")                           # core.py's own entry plus the entries its sibling modules add
check("loaded from a process that does not have scripts/ on its path, core.py still imports (it adds its folder itself)", code == 0 and count_absent is not None, text)
check("its folder goes to the front — scripts/spec.py wins over a spec.py next to the starting script",
      lambda: Path(got["SPEC"]) == SCRIPTS / "spec.py" and Path(got["FIRST"]) == SCRIPTS, text)
code, got, text = load_core("already")
check("when scripts/ is already on the path core.py does not add it a second time (the same number of entries as when it added it itself)",
      code == 0 and got.get("COUNT") is not None and got.get("COUNT") == count_absent, (count_absent, text))

# ───────── small deciding functions ─────────
print("names · thresholds")
check("_names: exactly n items are listed whole, without \"and 0 more\"", lambda: core._names(["a", "b", "c", "d"]) == "a, b, c, d", attempt(core._names, ["a", "b", "c", "d"]))
check("_names: one over n → the first n and \"and 1 more\"", lambda: core._names(["a", "b", "c", "d", "e"]) == "a, b, c, d and 1 more", attempt(core._names, ["a", "b", "c", "d", "e"]))
check("_names: n is taken from the call", lambda: core._names(["a", "b", "c"], 2) == "a, b and 1 more" and core._names(["a", "b"], 2) == "a, b")


def code_file(path, added, removed, new=False):
    return {"path": path, "kind": core.kind_of(path), "added": added, "removed": removed, "new": new, "status": "new" if new else "M"}


check("_structure: one new .py is not a structural change · two are (the threshold itself)",
      lambda: core._structure({"files": [code_file("a.py", 1, 0, True)]}) is None
      and core._structure({"files": [code_file("a.py", 1, 0, True), code_file("b.py", 1, 0, True)]}) == "2 new .py")
check("_manual_mutation: 29 changed lines of code without regex is nothing · 30 is the threshold itself",
      lambda: core._manual_mutation({"files": [code_file("a.py", 28, 1)], "input_parsing": [("a.py", 0)]}) is None
      and core._manual_mutation({"files": [code_file("a.py", 29, 1)], "input_parsing": [("a.py", 0)]}) == "30 changed lines of code")

repo = make_repo()
write(repo, "hooks/scripts/n0.py", "Y = 0\n")
write(repo, "hooks/scripts/n1.py", "Y = 1\n")
write(repo, "hooks/scripts/h.py", "X = 2\n")
write(repo, "tools/score.py", "def tally(x):\n    return x + 2\n")
p = attempt(core.plan, repo)
check("plan: two new .py (not tests) → Reasoning Review, reason \"2 new .py\"", lambda: reason_of(p, "Reasoning Review") == "2 new .py", p)
check("plan: four changed code files are all named in the Mutation reason (no \"and 0 more\")",
      lambda: reason_of(p, "Mutation Test") == "4 changed source .py (hooks/scripts/h.py, hooks/scripts/n0.py, hooks/scripts/n1.py, tools/score.py)", p)
write(repo, "tools/extra.py", "Z = 0\n")
p = attempt(core.plan, repo)
check("plan: five changed code files → the first four and \"and 1 more\"",
      lambda: reason_of(p, "Mutation Test") == "5 changed source .py (hooks/scripts/h.py, hooks/scripts/n0.py, hooks/scripts/n1.py, tools/extra.py and 1 more)", p)

repo = make_repo()
write(repo, "hooks/scripts/h.py", "".join("V%d = %d\n" % (n, n) for n in range(29)))
p = attempt(core.plan, repo)
check("plan: exactly 30 changed lines of code (+29 -1) → Manual Mutation Test, reason \"30 changed lines of code\"",
      lambda: p["files"][0]["added"] + p["files"][0]["removed"] == 30 and reason_of(p, "Manual Mutation Test") == "30 changed lines of code", p)

# ───────── the list of changes ─────────
print("changed — staged new file · deleted file · mode change · unreadable path")
repo = make_repo()
write(repo, "tools/staged.py", "A = 1\nB = 2\n")
git(repo, "add", "tools/staged.py")                       # in the index, not committed: `create mode` line of git diff HEAD --summary
(repo / "tools" / "score.py").unlink()                    # tracked file removed from disk: `delete mode` line
git(repo, "config", "core.filemode", "false")             # take the mode from the index on every system (Linux would read it from the file, and the change below would not show)
git(repo, "update-index", "--chmod=+x", "ui/a.html")      # a summary line that is neither create nor delete (`mode change`)
git(repo, "rm", "--cached", "-q", "README.md")            # out of the index but still on disk: in the diff (deleted) and in the untracked list
write(repo, "aaa.txt", "1\n2\n3\n")
(repo / "nested").mkdir()
git(repo, "init", "-q", str(repo / "nested"))             # an untracked repository is listed as the folder `nested/` — a path that cannot be read as a file
write(repo, "nested/inner.txt", "x\n")
files = attempt(core.changed, repo)
by_path = {f["path"]: f for f in files} if isinstance(files, list) else {}
check("changed: the list is read without raising and is sorted by path", isinstance(files, list) and [f["path"] for f in files] == sorted(by_path), files)
check("a staged new file is status A · new · its added lines",
      by_path.get("tools/staged.py") == {"path": "tools/staged.py", "status": "A", "new": True, "added": 2, "removed": 0}, by_path.get("tools/staged.py"))
check("a deleted tracked file is status D · not new · its removed lines",
      by_path.get("tools/score.py") == {"path": "tools/score.py", "status": "D", "new": False, "added": 0, "removed": 2}, by_path.get("tools/score.py"))
check("a file with only a mode change stays status M (a summary line that is not create · delete is passed over)",
      by_path.get("ui/a.html") == {"path": "ui/a.html", "status": "M", "new": False, "added": 0, "removed": 0}, by_path.get("ui/a.html"))
check("a path already in the diff is not overwritten by the untracked list (README.md keeps the diff's entry)",
      by_path.get("README.md") == {"path": "README.md", "status": "D", "new": False, "added": 0, "removed": 1}, by_path.get("README.md"))
check("an untracked file counts its lines · an untracked path that cannot be read counts 0 lines (not the count of the file before it)",
      by_path.get("aaa.txt", {}).get("added") == 3 and by_path.get("nested/") == {"path": "nested/", "status": "new", "new": True, "added": 0, "removed": 0},
      (by_path.get("aaa.txt"), by_path.get("nested/"), sorted(by_path)))
check("exactly these six paths (.oqa/ is left out)", sorted(by_path) == ["README.md", "aaa.txt", "nested/", "tools/score.py", "tools/staged.py", "ui/a.html"], sorted(by_path))
code, out = run_main(["plan", "--repo", str(repo)])
check("plan on the command line prints kind · status · line counts per file",
      code == 0 and "  source code A    +2    -0    tools/staged.py\n" in out and "  source code D    +0    -2    tools/score.py\n" in out, (code, out))

expected = hashlib.sha1()
expected.update(("README.md\0%s\n" % hashlib.sha1((repo / "README.md").read_bytes()).hexdigest()).encode("utf-8"))
expected.update(b"tools/score.py\0missing\n")
got = attempt(core.change_fingerprint, repo, [{"path": "README.md"}, {"path": "tools/score.py"}])
check("change_fingerprint: a file that is not there goes in as \"missing\" (not as the content of the file before it)",
      got == {"files": ["README.md", "tools/score.py"], "hash": expected.hexdigest()[:16]}, got)
check("added_text: a new file that cannot be read is empty text", lambda: core.added_text(repo, {"path": "tools/none.py", "new": True}) == "",
      attempt(core.added_text, repo, {"path": "tools/none.py", "new": True}))

print("changed — a repository without a single commit")
bare = Path(tempfile.mkdtemp()) / "fresh"
write(bare, "a.py", "A = 1\n")
write(bare, "b.txt", "b\n")
git(bare, "init", "-q")
git(bare, "add", "a.py")
files = attempt(core.changed, bare)
check("without a commit, a file in the index is a new file too (next to the untracked one)",
      files == [{"path": "a.py", "status": "new", "new": True, "added": 1, "removed": 0}, {"path": "b.txt", "status": "new", "new": True, "added": 1, "removed": 0}], files)
code, out = run_main(["plan", "--repo", str(bare)])
first = out.splitlines()[0] if out else ""
check("plan there: exit code 0 · the head line says HEAD none · spec none",
      code == 0 and first.startswith("Core plan · ") and first.endswith(" · HEAD none · spec none"), (code, out))
check("… and lists the indexed file with its kind", "  source code new  +1    -0    a.py\n" in out and "| Mutation Test | required | 1 changed source .py (a.py) |" in out, out)

print("changed — git's diff output is read whatever the user's git settings · a failed git call is unknown, not \"no changes\"")
PARSING = "import re\n\n\ndef tally(x):\n    a = re.split(r',', x)\n    b = re.match(r'x', x)\n    c = x.split()\n    return a, b, c\n"
repo = make_repo()
write(repo, "tools/score.py", PARSING)
plain_added = attempt(core.added_texts, repo, [{"path": "tools/score.py"}])
git(repo, "config", "color.ui", "always")                 # a user setting that colours every diff, also when its output is not a screen
git(repo, "config", "diff.external", "git --version")     # … and one that hands the diff to another program
coloured = attempt(core.added_texts, repo, [{"path": "tools/score.py"}])
p = attempt(core.plan, repo)
check("added lines are the same with `color.ui always` and `diff.external` set in the repository (the diff is asked for without colour and without outside programs)",
      lambda: coloured == plain_added and coloured["tools/score.py"].splitlines()[-1] == "    return a, b, c" and "\x1b" not in coloured["tools/score.py"], (plain_added, coloured))
check("… so the plan still picks the checks that depend on the added lines (Fuzz · Manual Mutation) — a coloured diff must not read as \"nothing added\"",
      lambda: "Fuzz Test (LLM)" in names(p) and "Manual Mutation Test" in names(p) and core.added_text(repo, {"path": "tools/score.py"}) == plain_added["tools/score.py"], p)
diff_calls = []
real_git = core._git
core._git = lambda repo_, *args: (diff_calls.append(args) if args[0] == "diff" else None, real_git(repo_, *args))[1]
try:
    attempt(core.plan, repo)
    attempt(core.added_text, repo, {"path": "tools/score.py"})
finally:
    core._git = real_git
check("every git diff call carries --no-color · --no-ext-diff · --no-textconv and the fixed prefixes --src-prefix=a/ · --dst-prefix=b/ (list of changes · added lines of all files · added lines of one file)",
      len(diff_calls) == 3 and all({"--no-color", "--no-ext-diff", "--no-textconv", "--src-prefix=a/", "--dst-prefix=b/"} <= set(args) for args in diff_calls), diff_calls)
for setting in ("diff.noPrefix", "diff.mnemonicPrefix"):
    repo = make_repo()
    write(repo, "tools/score.py", PARSING)
    plain_added = attempt(core.added_texts, repo, [{"path": "tools/score.py"}])
    git(repo, "config", setting, "true")                  # the file lines of the diff then read `+++ tools/score.py` or `+++ w/tools/score.py`
    prefixed = attempt(core.added_texts, repo, [{"path": "tools/score.py"}])
    p = attempt(core.plan, repo)
    check("added lines are the same with `%s` set in the repository, and the plan still picks Fuzz · Manual Mutation — the `+++ b/…` line was not recognised, so the added lines read as none and both checks dropped out" % setting,
          lambda: isinstance(plain_added, dict) and prefixed == plain_added and prefixed["tools/score.py"].splitlines()[-1] == "    return a, b, c"
          and "Fuzz Test (LLM)" in names(p) and "Manual Mutation Test" in names(p), (prefixed, p))


class _OtherPrefix:
    returncode, stderr = 0, ""
    stdout = "diff --git c/tools/score.py w/tools/score.py\nindex 1..2 100644\n--- c/tools/score.py\n+++ w/tools/score.py\n@@ -1,0 +2 @@\n+import re\n"


repo = make_repo()
write(repo, "tools/score.py", PARSING)
real_git, values = core._git, {}
core._git = lambda repo_, *git_args: _OtherPrefix() if git_args[0] == "diff" and "-U0" in git_args else real_git(repo_, *git_args)
try:
    got = attempt(core.added_texts, repo, [{"path": "tools/score.py"}], values)
    p = attempt(core.plan, repo)
finally:
    core._git = real_git
check("a diff whose file line is not `+++ b/…` (a prefix this tool did not ask for) is unknown — None with the line in the reason, and the plan is unknown too; never \"no added lines\"",
      lambda: got is None and "+++ w/tools/score.py" in values.get("error", "") and list(p) == ["unknown"] and "+++ w/tools/score.py" in p["unknown"], (got, values, p))


class _OtherFile:
    returncode, stderr = 0, ""
    stdout = "diff --git a/tools/score.py b/tools/other.py\nindex 1..2 100644\n--- a/tools/score.py\n+++ b/tools/other.py\n@@ -1,0 +2 @@\n+import re\n"


class _NoFileLine:
    returncode, stderr = 0, ""
    stdout = "diff --git a/tools/score.py b/tools/score.py\nindex 1..2 100644\n@@ -1,0 +2 @@\n+import re\n"


for shape, named in ((_OtherFile, "tools/other.py"), (_NoFileLine, "no file line")):
    values = {}
    core._git = lambda repo_, *git_args: shape() if git_args[0] == "diff" and "-U0" in git_args else real_git(repo_, *git_args)
    try:
        got = attempt(core.added_texts, repo, [{"path": "tools/score.py"}], values)
        p = attempt(core.plan, repo)
    finally:
        core._git = real_git
    check("added lines the diff gives for a file that is not one of the changed files (%s) are unknown — None with that name in the reason, and the plan is unknown; "
          "not a traceback and not \"no added lines\"" % named,
          lambda: got is None and values.get("error", "").endswith("cannot be matched to the changed files: %s" % named) and list(p) == ["unknown"] and named in p["unknown"], (got, values, p))


class _Failed:
    returncode, stdout, stderr = 128, "", "fatal: made to fail by the test\nsecond line\n"


def with_failing_git(fails, func, *args):
    """func(*args) while the git calls for which fails(args) is true exit 128 without output"""
    real = core._git
    core._git = lambda repo_, *git_args: _Failed() if fails(git_args) else real(repo_, *git_args)
    try:
        return attempt(func, *args)
    finally:
        core._git = real


repo = make_repo()
write(repo, "tools/score.py", PARSING)
write(repo, "fresh.txt", "new\n")
check("(setup) with git answering, the list of changes has both files", [f["path"] for f in attempt(core.changed, repo)] == ["fresh.txt", "tools/score.py"], attempt(core.changed, repo))
values = {}
got = with_failing_git(lambda a: a[0] == "diff", core.changed, repo, values)
check("git diff exits with an error → changed() is None (unknown), not a list without the tracked changes · the failed command and git's first error line are kept",
      got is None and values.get("error") == "git diff HEAD --numstat failed (exit code 128): fatal: made to fail by the test", (got, values))
got = with_failing_git(lambda a: a[0] == "ls-files", core.changed, repo)
check("git ls-files exits with an error → None too (the untracked files are unknown)", got is None, got)
p = with_failing_git(lambda a: a[0] == "diff", core.plan, repo)
check("plan then answers unknown and names the failed command — it does not write a plan with \"no changes\"",
      p == {"unknown": "git diff HEAD --numstat failed (exit code 128): fatal: made to fail by the test"} and not (repo / ".oqa" / "core.json").exists(), p)
p = with_failing_git(lambda a: a[0] == "diff" and "-U0" in a, core.plan, repo)
check("only the diff of the added lines fails → the plan is unknown as well (the checks picked from added lines would silently be left out)",
      p == {"unknown": "git diff HEAD -U0 failed (exit code 128): fatal: made to fail by the test"} and not (repo / ".oqa" / "core.json").exists(), p)
check("added_texts · added_text: None when that diff fails (not empty text)",
      with_failing_git(lambda a: a[0] == "diff", core.added_texts, repo, [{"path": "tools/score.py"}]) is None
      and with_failing_git(lambda a: a[0] == "diff", core.added_text, repo, {"path": "tools/score.py"}) is None)
code, out = with_failing_git(lambda a: a[0] == "diff", run_main, ["plan", "--repo", str(repo)])
check("plan on the command line there: returns 2 · one unknown line with the failed command",
      code == 2 and out == "unknown — git diff HEAD --numstat failed (exit code 128): fatal: made to fail by the test\n", (code, out))
attempt(core.plan, repo)
v = with_failing_git(lambda a: a[0] == "diff", core.verdict, repo)
check("verdict while git diff fails: the change now is unknown — to confirm, with the failed command; not \"the change differs\" and not DONE",
      v.get("verdict") != "DONE" and "git could not be read, so the change now is unknown (git diff HEAD --numstat failed (exit code 128): fatal: made to fail by the test)" in (v.get("to_confirm") or [])
      and not any("the change differs" in t for t in v.get("reasons") or []), v)
(repo / ".git" / "index").write_bytes(b"not an index\n")       # a real failure: git diff and git ls-files cannot read the index
got = attempt(core.changed, repo)
p = attempt(core.plan, repo)
check("a repository whose index git cannot read: changed() is None and the plan is unknown, naming git diff — it used to be an empty list, planned as \"no changes\"",
      lambda: got is None and list(p) == ["unknown"] and p["unknown"].startswith("git diff HEAD --numstat failed (exit code "), (got, p))

check("evidence.KNOWN_KINDS is the check names of core's rule table, in that order, plus the verification report — the two lists cannot drift apart",
      lambda: tuple(evidence.KNOWN_KINDS) == tuple(rule[0] for rule in core.RULES) + ("Verification Report",) and len(core.RULES) == 11, (getattr(evidence, "KNOWN_KINDS", None), [rule[0] for rule in core.RULES]))

# ───────── spec outside the repository ─────────
print("spec path")
repo = make_repo()
outside = Path(tempfile.mkdtemp()).resolve() / "elsewhere.md"
outside.write_text(BASE_SPEC, encoding="utf-8")
write(repo, ".oqa/current", str(outside) + "\n")
p = attempt(core.plan, repo)
check("a `.oqa/current` that names a file outside the specs folder is not taken as the spec, and the spec inside the folder is not silently taken in its place either — the plan has no spec",
      lambda: p["spec_file"] is None and (repo / SPEC_FILE).is_file(), p)
check("_relative: inside the repository it is the path from the root with forward slashes",
      lambda: core._relative(repo, repo / ".oqa" / "specs" / "x.md") == ".oqa/specs/x.md" and core._relative(repo, outside) == str(outside))

# ───────── verdict ─────────
print("verdict — cannot judge · plan again")
repo = make_repo()
write(repo, ".oqa/core.json", json.dumps({"checks": []}))
v = core.verdict(repo)
check("core.json without a created time → UNKNOWN · exit code 2 · that reason", v == {"verdict": "UNKNOWN", "exit_code": 2, "message": "core.json has no created time"}, v)
code, out = run_main(["verdict", "--repo", str(repo)])
check("… on the command line: returns 2 and prints only the verdict line (no table · no report line)",
      code == 2 and out == "Core verdict: UNKNOWN (exit code 2) — core.json has no created time\n", (code, out))
write(repo, ".oqa/core.json", json.dumps({"created": "T", "checks": []}))
real_fingerprint, core.fingerprint = core.fingerprint, lambda *a, **k: None
try:
    v = core.verdict(repo)
finally:
    core.fingerprint = real_fingerprint
check("when the fingerprint cannot be measured now → UNKNOWN · exit code 2 (not 3 — nothing is asked of the user) · that reason",
      v.get("verdict") == "UNKNOWN" and v.get("exit_code") == 2 and "cannot measure the code fingerprint now" in v.get("message", ""), v)
write(repo, ".oqa/core.json", json.dumps({"checks": []}))

# no changes: nothing to verify is not DONE; then the current spec is switched and removed
NO_CHANGE = "there is no uncommitted change to verify — core.py plan and verdict must be run before committing (after a commit nothing is left to check, so this is not DONE)"
repo = make_repo()
attempt(core.plan, repo)
v = core.verdict(repo)
check("no changes · approved → NEEDS DECISION (3), not DONE without checks — the reason says plan and verdict must be run before committing (committing first and asking afterwards used to give DONE with no evidence at all)",
      v.get("verdict") == "NEEDS DECISION" and v.get("exit_code") == 3 and v.get("to_confirm") == [NO_CHANGE] and v.get("message") == NO_CHANGE
      and v.get("checks") == [] and v.get("reasons") == [] and v.get("report") == "not required", v)
saved = json.loads((repo / ".oqa" / "core.json").read_text(encoding="utf-8"))
write(repo, ".oqa/core.json", json.dumps({k: value for k, value in saved.items() if k != "reason"}))
v = core.verdict(repo)
check("a core.json without a reason → the same answer (no \"no checks to pick\" DONE whatever the plan's reason field says)", v.get("verdict") == "NEEDS DECISION" and v.get("message") == NO_CHANGE, v)
write(repo, ".oqa/core.json", json.dumps(dict(saved, files=[{"path": "README.md", "kind": "doc", "status": "M", "added": 1, "removed": 0}])))
v = core.verdict(repo)
check("a core.json that lists changed files but no checks → not DONE either (an empty check list is never \"all checks have evidence\")",
      v.get("verdict") != "DONE" and "core.json lists changed files but no checks — nothing was verified; run core.py plan again" in (v.get("to_confirm") or []), v)
write(repo, ".oqa/core.json", json.dumps(saved))
code, out = run_main(["verdict", "--repo", str(repo)])
check("no changes on the command line: returns 3 · no check table · no gate line · no \"no spec\" hint · no \"verification report: not required\" line"
      " (rule changed: with nothing to verify that line read as if the work needed no report)",
      code == 3 and out.startswith("Core verdict: NEEDS DECISION (exit code 3) — " + NO_CHANGE + "\n") and "| check | required | state | evidence |" not in out
      and "Independent Review Gate:" not in out and "verification report" not in out and "hint:" not in out, (code, out))
write(repo, ".oqa/specs/2099-01-02_b.md", BASE_SPEC)
write(repo, ".oqa/current", "2099-01-02_b.md\n")
v = core.verdict(repo)
differs = "the current spec differs from the spec in core.json (%s) — run core.py plan again" % SPEC_FILE
check("the current spec is another file than the planned one → INCOMPLETE, asks to plan again (the no-change line stays to confirm)",
      v.get("verdict") == "INCOMPLETE" and v.get("reasons") == [differs] and v.get("to_confirm") == [NO_CHANGE], v)
shutil.rmtree(repo / ".oqa" / "specs")
v = core.verdict(repo)
check("the planned spec is gone → the same reason and \"no current spec found\" to confirm",
      v.get("verdict") == "INCOMPLETE" and v.get("reasons") == [differs] and v.get("to_confirm") == [NO_CHANGE, "no current spec found"], v)

repo = make_repo(spec_text=None)
p = attempt(core.plan, repo)
v = core.verdict(repo)
check("a repository without any spec: planned with spec None · the verdict is NEEDS DECISION (3) with \"no current spec found\"",
      lambda: p["spec_file"] is None and v["verdict"] == "NEEDS DECISION" and v["exit_code"] == 3 and v["to_confirm"] == [NO_CHANGE, "no current spec found"]
      and v["reasons"] == [] and v["message"] == NO_CHANGE + "; no current spec found", (p, v))
code, out = run_main(["plan", "--repo", str(repo)])
head = impact.head(repo) or "?"
check("plan there on the command line: HEAD is the commit (10 characters) · spec none · \"no checks\" with the reason",
      code == 0 and out.splitlines()[0].endswith(" · HEAD %s · spec none" % head[:10]) and out.endswith("\n0 changed file(s)\n\nno checks — no changes\n"), (code, out))

# committing first and asking afterwards
repo = make_repo()
write(repo, "tools/score.py", "def tally(x):\n    return x + 2\n")
git(repo, "add", "-A", "--", "tools")
git(repo, "commit", "-q", "-m", "the change, committed before any check ran")
p = attempt(core.plan, repo)
v = core.verdict(repo)
check("a change committed before plan and verdict: the plan sees no change, and the verdict is NEEDS DECISION (3) with that reason — not DONE with zero evidence",
      lambda: p["checks"] == [] and evidence.read(repo) == [] and v["verdict"] == "NEEDS DECISION" and v["exit_code"] == 3 and v["to_confirm"] == [NO_CHANGE], (p, v))

# `.oqa/current` that names no spec: the reason is said in place of "no current spec found", without the "write a spec" hint
print("verdict — .oqa/current names no spec · unclear gate line · damaged evidence file")
repo = make_repo()
write(repo, "README.md", "# Guide\nmore\n")
write(repo, ".oqa/current", "2099-01-01_typo.md\n")
p = attempt(core.plan, repo)
fp = evidence.fingerprint(repo)
record(repo, "Regression Test", "pass", fp)
record(repo, "Verification Report", "pass", fp)
v = core.verdict(repo)
names_typo = (".oqa/current names \"2099-01-01_typo.md\", which is not a spec file (.md) directly inside .oqa/specs (a missing file, a template, not a .md file or a path outside the folder)"
              " — correct it to name the spec to use")      # wording changed with the rule: an empty .oqa/current no longer means "the spec with the latest name"
check("`.oqa/current` names a missing file (a typo) while another spec with an approval is in the folder → the plan has no spec and the verdict is not DONE on the other spec's approval:"
      " INCOMPLETE (1) with the reason that names the value as its one reason — rule changed: it used to be NEEDS DECISION with the line under \"to confirm\"",
      lambda: p["spec_file"] is None and v["verdict"] == "INCOMPLETE" and v["exit_code"] == 1 and v["reasons"] == [names_typo] and v["to_confirm"] == [], (p, v))
code, out = run_main(["verdict", "--repo", str(repo)])
check("… on the command line: returns 1 · the reason is the headline · the \"there is no spec yet\" hint is not printed (a spec is there; the pointer is wrong)",
      code == 1 and out.splitlines()[0] == "Core verdict: INCOMPLETE (exit code 1) — " + names_typo and "hint:" not in out and "no current spec found" not in out, (code, out))
# the pointer is corrected but the plan is not made again: the plan was made with no spec at all (finish line · expected results · gate unread)
write(repo, ".oqa/current", "2099-01-01_a.md\n")
v = core.verdict(repo)
planned_without = "core.py plan ran without a usable spec (%s) — run core.py plan again" % names_typo
check("the plan ran while `.oqa/current` was wrong, then the pointer is corrected without a new plan → INCOMPLETE, asks to plan again — it used to be DONE with the checks of the spec (Requirements Traceability …) never picked",
      lambda: v["verdict"] == "INCOMPLETE" and v["reasons"] == [planned_without] and v["to_confirm"] == [], v)
write(repo, ".oqa/current", "2099-01-01_a.md\n")
p = attempt(core.plan, repo)
v = core.verdict(repo)
check("… with the pointer corrected and planned again → DONE", lambda: p["spec_file"] == SPEC_FILE and v["verdict"] == "DONE" and v["to_confirm"] == [], (p, v))
shutil.rmtree(repo / ".oqa" / "specs")
write(repo, ".oqa/core.json", json.dumps(dict(json.loads((repo / ".oqa" / "core.json").read_text(encoding="utf-8")), spec_file=None)))
code, out = run_main(["verdict", "--repo", str(repo)])
check("the real \"no spec\" case (no specs folder) still says \"no current spec found\" and prints the hint — whatever .oqa/current says",
      code == 3 and out.splitlines()[0].endswith(" — no current spec found") and "\nhint: there is no spec yet" in out, (code, out))

# gate line that cannot be read
UNCLEAR_SPEC = BASE_SPEC + "\n## Independent review gate\n- Independent review gate: on (the user said yes on 2099-01-01)\n"
repo = make_repo(spec_text=UNCLEAR_SPEC)
write(repo, "README.md", "# Guide\nmore\n")
p = attempt(core.plan, repo)
fp = evidence.fingerprint(repo)
record(repo, "Regression Test", "pass", fp)
record(repo, "Verification Report", "pass", fp)
v = core.verdict(repo)
unclear = ("the spec's last \"Independent review gate:\" line is neither `off` nor `on (… \"user words\")`, so it cannot be told whether the gate is on: "
           "- Independent review gate: on (the user said yes on 2099-01-01)")
check("a gate line that is neither a clean off nor an on with quoted words (on without quotes) → NEEDS DECISION (3), the line is quoted to confirm — not DONE as if the gate were off",
      lambda: names(p) == ["Regression Test"] and v["verdict"] == "NEEDS DECISION" and v["exit_code"] == 3 and v["to_confirm"] == [unclear] and v["review_gate"] is None, (p, v))
write(repo, SPEC_FILE, BASE_SPEC + "\n## Independent review gate\n- Independent review gate: off   <- to switch it on: `on (<date> \"<user's words>\")`\n")
v = core.verdict(repo)
check("the template's clean off line (with its hint) is off: DONE, nothing to confirm", v.get("verdict") == "DONE" and v.get("to_confirm") == [] and v.get("review_gate") is None, v)
write(repo, SPEC_FILE, BASE_SPEC + "\n## Independent Review Gate\n- Independent Review Gate: On (2099-01-01 \"use the review gate\")   <- switched on\n")
p = attempt(core.plan, repo)
v = core.verdict(repo)
check("an on line with capitals and a trailing hint is on: the plan picks the Independent Review Gate and the verdict wants its evidence (it used to be read as off)",
      lambda: names(p) == ["Regression Test", "Independent Review Gate"] and v["verdict"] == "INCOMPLETE" and v["missing_checks"] == ["Independent Review Gate"]
      and v["review_gate"] == {"rounds": 0, "approved": False}, (p, v))
# the gate's approval is for the code AND what the spec promised then — the spec is outside the fingerprint
import contextlib      # noqa: E402
import io              # noqa: E402
GATE_SPEC = BASE_SPEC + "\n## Independent Review Gate\n- Independent Review Gate: On (2099-01-01 \"use the review gate\")   <- switched on\n"
fp = evidence.fingerprint(repo)
record(repo, "Regression Test", "pass", fp)
record(repo, "Verification Report", "pass", fp)
with contextlib.redirect_stdout(io.StringIO()):
    gate_code = evidence.main(["record", "--repo", str(repo), "--kind", "Independent Review Gate", "--result", "approved", "--basis", "round 1, findings 0", "--fingerprint", fp])
gate_line = evidence.read(repo)[-1]
v = core.verdict(repo)
check("`evidence.py record` of the gate stores what the spec promised then (spec_hash) · approved → DONE",
      gate_code == 0 and gate_line.get("spec_hash") == evidence.spec_hash(GATE_SPEC) and v.get("verdict") == "DONE" and v.get("review_gate") == {"rounds": 1, "approved": True}, (gate_line, v))
write(repo, SPEC_FILE, GATE_SPEC.replace("## Expected results", "## Expected results\n- a promise the reviewer never saw"))
v = core.verdict(repo)
check("an expected result changed after the gate approved → the approval no longer counts (INCOMPLETE · run the gate again)",
      v.get("verdict") == "INCOMPLETE" and "the spec's expected results or finish line changed after the independent review gate approved — run the gate again" in v.get("reasons", [])
      and v.get("review_gate") == {"rounds": 1, "approved": False}, v)
write(repo, SPEC_FILE, GATE_SPEC)
check("… with the spec as it was when the gate answered the approval stands again", core.verdict(repo).get("verdict") == "DONE", core.verdict(repo))
# the gate answers about a spec — while it cannot be told which one, the answer is not recorded (the line would carry no spec hash)
write(repo, ".oqa/specs/second.md", GATE_SPEC)
(repo / ".oqa" / "current").unlink(missing_ok=True)
lines_before = len(evidence.read(repo))
refused = io.StringIO()
with contextlib.redirect_stdout(refused):
    gate_code = evidence.main(["record", "--repo", str(repo), "--kind", "Independent Review Gate", "--result", "approved", "--fingerprint", fp])
check("with two specs and no `.oqa/current` the gate answer is refused (exit code 2 · the reason · nothing recorded)",
      gate_code == 2 and "the Independent Review Gate is not recorded" in refused.getvalue() and len(evidence.read(repo)) == lines_before, (gate_code, refused.getvalue()))
(repo / ".oqa" / "specs" / "second.md").unlink()

# evidence file with a line that cannot be read
repo = make_repo()
write(repo, "README.md", "# Guide\nmore\n")
attempt(core.plan, repo)
fp = evidence.fingerprint(repo)
record(repo, "Regression Test", "pass", fp)
record(repo, "Verification Report", "pass", fp)
check("(setup) whole evidence lines → DONE", core.verdict(repo).get("verdict") == "DONE", core.verdict(repo))
with open(repo / ".oqa" / "evidence.jsonl", "a", encoding="utf-8") as handle:
    handle.write('{"time": "2099-01-01T10:00:00", "kind": "Regression Test", "fingerprint": "%s", "result": "fail", "basis": "exit co\n\n' % fp)      # a later fail, cut off while being written
v = core.verdict(repo)
damaged_line = "1 line(s) of .oqa/evidence.jsonl cannot be read as evidence records — a damaged line may hide a failure; repair the file or record the evidence again"
check("a garbled later line in the evidence file (a cut-off fail) → NEEDS DECISION (3), the damage is raised to confirm — the earlier pass does not stand unnoticed",
      v.get("verdict") == "NEEDS DECISION" and v.get("exit_code") == 3 and v.get("to_confirm") == [damaged_line] and v.get("reasons") == [], v)

# docs-only change: report required, git unreadable, gate switched on after the plan
print("verdict — report · git unreadable · gate switched on after the plan")
repo = make_repo()
write(repo, "README.md", "# Guide\nmore\n")
p = attempt(core.plan, repo)
fp = evidence.fingerprint(repo)
v = core.verdict(repo)
check("Regression alone is required → the report is required too: \"no report\"",
      lambda: names(p) == ["Regression Test"] and v["report"] == "no report" and v["reasons"] == ["no verification report — run report.py", "missing check: Regression Test"], v)
record(repo, "Regression Test", "pass", fp)
record(repo, "Verification Report", "pass", fp)
v = core.verdict(repo)
check("with both recorded → DONE, the message counts the required checks",
      v.get("verdict") == "DONE" and v.get("message") == "all 1 required check(s) have evidence for the code now (fingerprint %s)" % fp and v.get("review_gate") is None, v)
# The report measured a change against a commit; the fingerprint holds only the files. After `git reset --soft` the files are the same and the change is bigger
evidence.record(repo, "Verification Report", "pass", "recorded by the test", fp, "core_gaps_test", head=impact.head(repo))
v = core.verdict(repo)
check("a report line that names the HEAD now is accepted (DONE)", v.get("verdict") == "DONE" and v.get("report") == "all passed", v)
evidence.record(repo, "Verification Report", "pass", "recorded by the test", fp, "core_gaps_test", head="0" * 40)
v = core.verdict(repo)
check("a report made against another commit is not evidence for the change now, although the fingerprint is the same → INCOMPLETE · run report.py again",
      v.get("verdict") == "INCOMPLETE" and v.get("report") == "made against another commit"
      and v.get("reasons") == ["the verification report was made against another commit (HEAD 0000000 then) — the change it measured is not the change now; run report.py again"], v)
record(repo, "Verification Report", "pass", fp)
check("(setup) a later report line without the key is accepted as before", core.verdict(repo).get("verdict") == "DONE", core.verdict(repo))
# a blind review written into the spec's Tools section after the plan — in no check list, so the verdict did not ask for it
write(repo, SPEC_FILE, BASE_SPEC + "\n## Tools\n- Reviewing: blind review of the refund rule\n")
v = core.verdict(repo)
check("a blind review named in \"## Tools\" after core.py plan → INCOMPLETE · plan again (it used to stay DONE, and a second plan made it required)",
      v.get("verdict") == "INCOMPLETE" and v.get("reasons") == ["the spec asks for more than it did at core.py plan (a blind review in \"## Tools\") — run core.py plan again"], v)
write(repo, SPEC_FILE, BASE_SPEC + "\n## Tools\n- Reviewing: blind review: not used — nothing high-risk here\n")
check("… a Tools line that says the blind review is not used asks for nothing more (DONE as before)", core.verdict(repo).get("verdict") == "DONE", core.verdict(repo))
old_core = json.loads((repo / ".oqa" / "core.json").read_text(encoding="utf-8"))
write(repo, SPEC_FILE, BASE_SPEC + "\n## Tools\n- Reviewing: blind review of the refund rule\n")
write(repo, ".oqa/core.json", json.dumps({k: v_ for k, v_ in old_core.items() if k != "spec_picks"}))
check("… a core.json of an older plan (no `spec_picks`) is judged as before", core.verdict(repo).get("verdict") == "DONE", core.verdict(repo))
attempt(core.plan, repo)
v = core.verdict(repo)
check("… once the plan was made with that Tools line the blind review is a missing check, and the verdict no longer says the spec asks for more",
      v.get("missing_checks") == ["Blind Review"] and not any("asks for more" in r for r in v.get("reasons", [])), v)
write(repo, ".oqa/core.json", json.dumps(old_core))
write(repo, SPEC_FILE, BASE_SPEC)
check("(setup) back to the base spec → DONE", core.verdict(repo).get("verdict") == "DONE", core.verdict(repo))
real_changed = core.changed
core.changed = lambda repo_, values=None: None           # git stops answering between the fingerprint and the list of changes
try:
    v = core.verdict(repo)
finally:
    core.changed = real_changed
check("the list of changes cannot be read → NEEDS DECISION (3), to confirm — not DONE and not \"the change differs\"",
      v.get("verdict") == "NEEDS DECISION" and v.get("exit_code") == 3 and v.get("to_confirm") == ["git could not be read, so the change now is unknown"] and v.get("reasons") == [], v)
code, out = run_main(["plan", "--repo", str(repo)])
check("plan on the command line: HEAD · spec file in the head line · finish line absent → says every picked check is required · no unknown-names line",
      code == 0 and out.splitlines()[0].endswith(" · HEAD %s · spec %s" % ((impact.head(repo) or "?")[:10], SPEC_FILE))
      and "\nfinish line: the spec has no \"## Finish line\" — every check the rule table picked is required\n" in out
      and "finish-line names not in the rule table" not in out and out.endswith("| Regression Test | required | 1 changed file(s) |\n"), (code, out))
write(repo, SPEC_FILE, GATE_SPEC)
v = core.verdict(repo)
no_gate_check = "the spec's independent review gate is on but core.json has no such check — run core.py plan again"
check("the gate is switched on in the spec after the plan → INCOMPLETE, asks to plan again",
      v.get("verdict") == "INCOMPLETE" and v.get("reasons") == [no_gate_check] and v.get("review_gate") == {"rounds": 0, "approved": False}, v)
p = attempt(core.plan, repo)
v = core.verdict(repo)
check("planned again → the gate is a check, the \"no such check\" reason is gone, only its evidence is missing",
      lambda: names(p) == ["Regression Test", "Independent Review Gate"] and v["reasons"] == ["missing check: Independent Review Gate"], (p, v))
code, out = run_main(["verdict", "--repo", str(repo)])
check("verdict on the command line: returns 1 · check table · findings counts · report · gate rounds · fingerprint",
      code == 1 and out.startswith("Core verdict: INCOMPLETE (exit code 1) — missing check: Independent Review Gate\n")
      and "\n| check | required | state | evidence |\n|---|---|---|---|\n| Regression Test | required | ok | " in out
      and "\n| Independent Review Gate | required | missing | no evidence recorded for this check |\n" in out
      and "\nopen findings: high 0 · medium 0 · low 0\n" in out and "\nverification report: all passed\n" in out
      and "\nIndependent Review Gate: 0 round(s) · approval for the code now no\n" in out and out.endswith("\nfingerprint now: %s\n" % fp), (code, out))
record(repo, "Independent Review Gate", "approved", fp)
code, out = run_main(["verdict", "--repo", str(repo)])
check("after the approval: returns 0 · the gate line says 1 round · yes",
      code == 0 and out.startswith("Core verdict: DONE (exit code 0) — all 2 required check(s)") and "\nIndependent Review Gate: 1 round(s) · approval for the code now yes\n" in out, (code, out))

# the report evidence says which spec it was made for
print("verdict — a report made for another spec · plan — why there is no spec · headings in other letter case")
repo = make_repo()
write(repo, "README.md", "# Guide\nmore\n")
write(repo, ".oqa/specs/2099-01-02_b.md", BASE_SPEC)
attempt(core.plan, repo)
fp = evidence.fingerprint(repo)
record(repo, "Regression Test", "pass", fp)
evidence.record(repo, "Verification Report", "pass", "all passed", fp, "report.py", spec=".oqa/specs/2099-01-02_b.md")
v = core.verdict(repo)
other_spec = "the verification report was made for another spec (.oqa/specs/2099-01-02_b.md) — run report.py with the current spec"
check("the report evidence for the code now names another spec than the current one → INCOMPLETE with that reason — a report made for spec B is not evidence under spec A (the fingerprint leaves .oqa/ out, so it could not tell; this used to be DONE)",
      v.get("verdict") == "INCOMPLETE" and v.get("exit_code") == 1 and v.get("reasons") == [other_spec] and v.get("report") == "made for another spec", v)
evidence.record(repo, "Verification Report", "pass", "all passed", fp, "report.py", spec=SPEC_FILE)
v = core.verdict(repo)
check("a later report line that names the current spec → DONE (the latest line for the code now counts) · a line without the key is accepted as before (the DONE checks above)",
      v.get("verdict") == "DONE" and v.get("reasons") == [] and v.get("report") == "all passed", v)
evidence.record(repo, "Verification Report", "fail", "incomplete 1 — gap x", fp, "report.py", spec=".oqa/specs/2099-01-02_b.md")
v = core.verdict(repo)
check("a failed report made for another spec keeps both reasons",
      v.get("verdict") == "INCOMPLETE" and v.get("reasons") == ["verification report: %s fail · incomplete 1 — gap x" % evidence.read(repo)[-1]["time"], other_spec], v)

# plan says why there is no spec
repo = make_repo()
write(repo, "README.md", "# Guide\nmore\n")
write(repo, ".oqa/specs/2099-01-02_b.md", BASE_SPEC)
(repo / ".oqa" / "current").unlink()
p = attempt(core.plan, repo)
several = (".oqa/current is missing or empty and .oqa/specs holds 2 spec files (2099-01-01_a.md, 2099-01-02_b.md), so the current spec is unknown"
           " — write the file name of the spec to use into .oqa/current")
check("plan without .oqa/current and two specs: no spec is picked (neither by name), and the reason is in the result and in core.json under spec_problem",
      lambda: p["spec_file"] is None and p["spec_problem"] == several and json.loads((repo / ".oqa" / "core.json").read_text(encoding="utf-8"))["spec_problem"] == several, p)
code, out = run_main(["plan", "--repo", str(repo)])
check("… and plan prints it on its own line under the head line (it used to show only `spec none` until the verdict)",
      code == 0 and out.splitlines()[0].endswith(" · spec none") and out.splitlines()[1] == "spec problem: " + several, (code, out))
v = core.verdict(repo)
check("… the verdict gives the same line as its first reason (rule changed: it used to stand under \"to confirm\")", v.get("verdict") == "INCOMPLETE" and (v.get("reasons") or [None])[0] == several, v)
write(repo, ".oqa/current", "2099-01-01_typo.md\n")
p = attempt(core.plan, repo)
check("plan with a `.oqa/current` that names no spec: spec_problem names the value", lambda: p["spec_file"] is None and p["spec_problem"] == names_typo, p)
write(repo, ".oqa/current", "2099-01-01_a.md\n")
p = attempt(core.plan, repo)
code, out = run_main(["plan", "--repo", str(repo)])
check("with the spec named: spec_problem is None and no such line is printed", lambda: p["spec_file"] == SPEC_FILE and p["spec_problem"] is None and "spec problem" not in out, (p, out))
p = attempt(core.plan, make_repo(spec_text=None))
check("a repository without any spec: spec_problem is None (that is the plain \"no spec\" case)", lambda: p["spec_file"] is None and p["spec_problem"] is None, p)

# headings typed in other letter case are still the sections
got = attempt(core.read_finish_line, "## finish line\n- Regression Test · Mutation Test\n")
check("read_finish_line: `## finish line` (small letters) is the finish line — it used to read as \"no finish line\"", got == ({"Regression Test", "Mutation Test"}, []), got)
cased = BASE_SPEC.replace("## Expected results", "## TOOLS\n- Reviewing: blind review of the refund rule\n\n## Expected Results")
repo = make_repo(spec_text=cased)
write(repo, "tools/score.py", "def tally(x):\n    return x + 2\n")
p = attempt(core.plan, repo)
check("plan with `## TOOLS` and `## Expected Results`: Blind Review and Requirements Traceability are picked — they used to drop out of the plan without a word",
      lambda: "## Expected Results" in cased and "Blind Review" in names(p) and "Requirements Traceability" in names(p), p)

# finish line: only a review is required — the report is not
print("verdict — the report is required only by a required Regression · Mutation · Traceability")
finish_spec = BASE_SPEC.replace("## Expected results", "## Finish line\n- Silent Failure Review\n\n## Expected results")
repo = make_repo(spec_text=finish_spec)
write(repo, "tools/score.py", "def tally(x):\n    return x + 2\n")
p = attempt(core.plan, repo)
fp = evidence.fingerprint(repo)
check("(setup) the finish line names Silent alone: the other three are recommended",
      lambda: {c["name"]: c["required"] for c in p["checks"]} == {"Regression Test": False, "Mutation Test": False, "Requirements Traceability": False, "Silent Failure Review": True}, p)
record(repo, "Silent Failure Review", "done", fp)
v = core.verdict(repo)
check("Regression · Mutation · Traceability only recommended → report \"not required\" · DONE with the one required check",
      v.get("verdict") == "DONE" and v.get("report") == "not required" and v.get("reasons") == []
      and v.get("message") == "all 1 required check(s) have evidence for the code now (fingerprint %s)" % fp, v)
code, out = run_main(["plan", "--repo", str(repo)])
check("plan on the command line: names the finish line · required and recommended rows · no unknown-names line",
      code == 0 and "\nfinish line: the spec's \"## Finish line\" — Silent Failure Review\n" in out and "\n| Regression Test | recommended | 1 changed file(s) |\n" in out
      and "\n| Silent Failure Review | required | code file name contains score |\n" in out and "finish-line names not in the rule table" not in out, (code, out))
finding = attempt(findings.add, repo, "reviewer", "high", "a wrong count")
code, out = run_main(["verdict", "--repo", str(repo)])
check("an open high finding: returns 1 · the findings line counts it · recommended rows are marked · report not required",
      code == 1 and "open high findings 1: F-1" in out.splitlines()[0] and "\nopen findings: high 1 · medium 0 · low 0\n" in out
      and "\n| Regression Test | recommended | missing | no evidence recorded for this check |\n" in out and "\n| Silent Failure Review | required | ok | " in out
      and "\nverification report: not required\n" in out and "Independent Review Gate:" not in out and out.endswith("\nfingerprint now: %s\n" % fp), (code, out, finding))
write(repo, SPEC_FILE, finish_spec.replace("- Silent Failure Review\n", "- Silent Failure Review · NoSuchCheck\n"))
code, out = run_main(["plan", "--repo", str(repo)])
check("a finish-line name that is not in the rule table is printed by plan", code == 0 and "\nfinish-line names not in the rule table: NoSuchCheck\n" in out, (code, out))

# the spec known now is compared with the one the plan used — always, also when the plan had none
print("verdict — the spec appeared, disappeared or changed after the plan or the report · the approval text · an unreadable findings ledger")
repo = make_repo(spec_text=None)
write(repo, "README.md", "# Guide\nmore\n")
p = attempt(core.plan, repo)
fp = evidence.fingerprint(repo)
record(repo, "Regression Test", "pass", fp)
record(repo, "Verification Report", "pass", fp)
write(repo, ".oqa/current", "2099-01-01_a.md\n")
write(repo, SPEC_FILE, BASE_SPEC)
v = core.verdict(repo)
check("planned when there was no spec, then a spec appears → INCOMPLETE \"… differs from the spec in core.json (none) — run core.py plan again\" — the comparison used to run only when core.json named a spec, so this was DONE",
      lambda: p["spec_file"] is None and p["spec_problem"] is None and v["verdict"] == "INCOMPLETE"
      and v["reasons"] == ["the current spec differs from the spec in core.json (none) — run core.py plan again"], (p, v))
attempt(core.plan, repo)
check("… planned again → DONE", core.verdict(repo).get("verdict") == "DONE", core.verdict(repo))
saved = json.loads((repo / ".oqa" / "core.json").read_text(encoding="utf-8"))
write(repo, ".oqa/core.json", json.dumps(dict(saved, spec_problem="the plan could not tell the spec")))
v = core.verdict(repo)
check("a core.json that carries a spec_problem is never DONE, whatever else it says: INCOMPLETE, asks to plan again and repeats the problem",
      v.get("verdict") == "INCOMPLETE" and v.get("reasons") == ["core.py plan ran without a usable spec (the plan could not tell the spec) — run core.py plan again"], v)
write(repo, ".oqa/core.json", json.dumps(saved))

# `.oqa/current` goes wrong after a good plan: that is the headline, and "differs from core.json" is not put on top of it
write(repo, ".oqa/current", "2099-01-01_typo.md\n")
v = core.verdict(repo)
check("`.oqa/current` is wrong after a good plan → its reason is the first (and only) reason; no \"the current spec differs … run core.py plan again\" on top"
      " — that headline sent the reader to plan again, which then ran without a spec",
      v.get("verdict") == "INCOMPLETE" and v.get("reasons") == [names_typo] and v.get("to_confirm") == [], v)
code, out = run_main(["verdict", "--repo", str(repo)])
check("… on the command line the first line is that reason, and nothing about core.json or \"also to confirm\" follows",
      code == 1 and out.splitlines()[0] == "Core verdict: INCOMPLETE (exit code 1) — " + names_typo and "differs from the spec in core.json" not in out and "also to confirm" not in out, (code, out))
record(repo, "Regression Test", "fail", fp)
v = core.verdict(repo)
check("… and it stays the first reason when other reasons follow", (v.get("reasons") or [None])[0] == names_typo and len(v.get("reasons") or []) > 1, v)

# the report evidence carries a hash of the spec's expected results and finish line
SPEC_CHANGED = "the spec's expected results or finish line changed after the report — run report.py again"
repo = make_repo()
write(repo, "README.md", "# Guide\nmore\n")
attempt(core.plan, repo)
fp = evidence.fingerprint(repo)
record(repo, "Regression Test", "pass", fp)
evidence.record(repo, "Verification Report", "pass", "all passed", fp, "report.py", spec=SPEC_FILE, spec_hash=attempt(lambda: evidence.spec_hash(BASE_SPEC)))
check("(setup) the report evidence carries the hash of the spec as it is → DONE", core.verdict(repo).get("verdict") == "DONE", core.verdict(repo))
write(repo, SPEC_FILE, BASE_SPEC.replace("- E1 the screen opens\n", "- E1 the screen opens\n- E2 the screen closes\n"))
v = core.verdict(repo)
check("an expected result (`- E2 …`) added to the spec after a passing report → INCOMPLETE with that reason · report \"spec changed after report\" — it used to stay DONE (the fingerprint leaves .oqa/ out)",
      v.get("verdict") == "INCOMPLETE" and v.get("reasons") == [SPEC_CHANGED] and v.get("report") == "spec changed after report", v)
write(repo, SPEC_FILE, BASE_SPEC.replace("## Results\n", "## Results\n### Verification Report\nall passed\n").replace("\"yes, go ahead\"", "\"yes, go ahead now\""))
v = core.verdict(repo)
check("the report written under Results and other sections are not part of the hash: DONE again", v.get("verdict") == "DONE" and v.get("report") == "all passed", v)
write(repo, SPEC_FILE, BASE_SPEC.replace("## Approval", "## Finish line\n- Regression Test\n\n## Approval"))
v = core.verdict(repo)
check("a finish line added after the report gives that reason too (next to \"changed after core.py plan\")",
      v.get("verdict") == "INCOMPLETE" and SPEC_CHANGED in (v.get("reasons") or []) and len(v.get("reasons") or []) == 2, v)

# what the verdict says about a missing approval
placeholder_spec = BASE_SPEC.replace("- 2099-01-01 · \"yes, go ahead\"", "- <date> · \"<user's words, exactly as they said them>\"   <- without a quoted line here the verdict stays INCOMPLETE")
repo = make_repo(spec_text=placeholder_spec)
write(repo, "README.md", "# Guide\nmore\n")
attempt(core.plan, repo)
fp = evidence.fingerprint(repo)
record(repo, "Regression Test", "pass", fp)
record(repo, "Verification Report", "pass", fp)
v = core.verdict(repo)
check("the approval section holds only the template's placeholder line → INCOMPLETE; the reason says it holds no quoted approval and names the placeholder — it used to say the section \"is empty\"",
      "<user's words" in placeholder_spec and v.get("verdict") == "INCOMPLETE"
      and v.get("reasons") == ["the spec's \"## Approval\" holds no quoted approval — only the template's placeholder line is there (replace it with the date and the user's own words in double quotes)"], v)

# a findings ledger that cannot be read is said so — never read as "no findings"
repo = make_repo()
write(repo, "README.md", "# Guide\nmore\n")
attempt(core.plan, repo)
fp = evidence.fingerprint(repo)
record(repo, "Regression Test", "pass", fp)
record(repo, "Verification Report", "pass", fp)
check("(setup) no findings ledger → DONE", core.verdict(repo).get("verdict") == "DONE", core.verdict(repo))
ledger = repo / ".oqa" / "findings.jsonl"
ledger.write_bytes(b'{"id": "F-1", "severity": "high", "status": "open", "what": "caf\xe9"}\n')      # not valid UTF-8
v = core.verdict(repo)
check("a findings ledger that is not valid UTF-8 → not DONE: \"cannot read the findings ledger\" to confirm · the open findings are unknown (None), not zero",
      v.get("verdict") == "NEEDS DECISION" and any(t.startswith("cannot read the findings ledger — ") for t in v.get("to_confirm") or []) and v.get("open_findings") is None, v)
got = attempt(findings.read, repo)
check("findings.read raises on it (a ValueError) instead of returning an empty list", isinstance(got, str) and got.startswith("raised Unicode"), got)
ledger.unlink()
ledger.mkdir()                                            # a folder where the file should be: reading fails with a system error
v = core.verdict(repo)
check("a findings ledger that cannot be opened at all (a folder in its place) → UNKNOWN (2) with the error — not DONE as if there were no findings",
      v.get("verdict") == "UNKNOWN" and v.get("exit_code") == 2 and "error during verdict" in v.get("message", "") and "findings.jsonl" in v.get("message", ""), v)
ledger.rmdir()

# one definition of "plain text file" for the plan and the report
repo = make_repo()
for path, text in (("notes.txt", "n\n"), ("LICENSE", "l\n"), (".gitignore", "__pycache__/\nbuild/\n"), ("requirements.txt", "x==1\n"), ("docs/guide.rst", "g\n")):
    write(repo, path, text)
p = attempt(core.plan, repo)
check("plan kinds: known text files are \"doc\" (notes.txt · LICENSE · guide.rst — .txt used to be \"unknown\") · a changed `.gitignore` and `requirements.txt` are \"unknown\" (not seen)",
      lambda: {f["path"]: f["kind"] for f in p["files"]} == {".gitignore": "unknown", "LICENSE": "doc", "docs/guide.rst": "doc", "notes.txt": "doc", "requirements.txt": "unknown"}, p)

# ───────── printing functions called directly ─────────
print("printing")


def printed(func, value):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        raised = attempt(func, value)
    return out.getvalue() if raised is None else str(raised)


got = printed(core.print_verdict, {"verdict": "DONE", "exit_code": 0, "message": "m", "checks": [{"name": "Old Check", "state": "ok", "basis": "b"}, {"name": "Side", "state": "missing", "basis": "n", "required": False}]})
check("print_verdict: a check row without a \"required\" field (an old record) is shown as required · nothing else is printed for missing fields",
      got == "Core verdict: DONE (exit code 0) — m\n\n| check | required | state | evidence |\n|---|---|---|---|\n| Old Check | required | ok | b |\n| Side | recommended | missing | n |\n", got)
got = printed(core.print_verdict, {"verdict": "UNKNOWN", "exit_code": 2, "message": "m"})
check("print_verdict: an UNKNOWN answer (verdict · exit code · message only) is one line", got == "Core verdict: UNKNOWN (exit code 2) — m\n", got)
got = printed(core.print_plan, {"unknown": "not a git repository: x"})
check("print_plan: an unknown answer is one line", got == "unknown — not a git repository: x\n", got)

print("rules")
table = attempt(core.rules_table_text)
rows = table.splitlines() if isinstance(table, str) else []
check("rules_table_text: header · one row per rule with the check name and its condition",
      rows[:3] == ["| check | condition |", "|---|---|", "| Regression Test | always when anything changed |"] and len(rows) == 2 + len(core.RULES)
      and "| E2E UI Test | ui files (.html · .css · .js) changed |" in rows and rows[-1] == "| Baseline | no impact map (repository seen for the first time) |", table)
code, out = run_main(["rules"])
check("main rules: returns 0 (the number, not None) and prints the table", code == 0 and code is not None and out == str(table) + "\n", (code, out[:200]))
code, out = command_line("rules")
check("core.py rules as a process: exit code 0 · the same table", code == 0 and out == str(table) + "\n", (code, out[:200]))
code, out = command_line("plan", "--repo", tempfile.mkdtemp())
check("core.py plan as a process in a folder that is not git: exit code 2 · unknown", code == 2 and out.startswith("unknown — not a git repository: "), (code, out))

print("\nfailures: %d" % len(failures))
sys.exit(1 if failures else 0)
