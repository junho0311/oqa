# -*- coding: utf-8 -*-
"""Tests of the edge decisions inside `scripts/evidence.py` and `scripts/spec.py`:
the state and basis text of `check_evidence`, the exit codes and printed phrases of the evidence CLI, when a fingerprint
counts as measured, which file `current_spec` picks, and what counts as an approval or an "on" review gate.

Every answer is fixed in advance and compared as a whole value (not "contains something"), so that a changed decision shows as a FAIL line.
"""
import _harness  # noqa: F401,E402 — collects the temp folders in one place for this test and removes it at the end
import contextlib
import io
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
import evidence  # noqa: E402
import spec  # noqa: E402

failures = []


def check(name, condition, text=""):
    print(("  ok  " if condition else "  FAIL  ") + name)
    if not condition:
        failures.append(name)
        if text != "":
            print("        " + str(text)[:600].replace("\n", " / "))


def call(function, *args, **kwargs):
    """The value the function returns — an exception becomes a value too, so that a broken decision is a FAIL line of the check that looks at it, not a crash of the file"""
    try:
        return function(*args, **kwargs)
    except Exception as error:                            # noqa: BLE001
        return ("raised", repr(error))


def git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), "-c", "user.email=t@t", "-c", "user.name=t"] + list(args), capture_output=True, check=True)


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


def new_folder(files=None):
    root = Path(tempfile.mkdtemp()) / "plain"
    root.mkdir()
    for name, text in (files or {}).items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text, encoding="utf-8")
    return root


def run_cli(*args):
    done = subprocess.run([sys.executable, str(TOOLS / "evidence.py")] + [str(a) for a in args], capture_output=True, text=True, encoding="utf-8", errors="replace")
    return done.returncode, done.stdout + done.stderr


def run_main(*args):
    """(return value of evidence.main, printed text) in this process — a process exit code cannot tell `return 0` from `return None`"""
    printed = io.StringIO()
    with contextlib.redirect_stdout(printed):
        code = call(evidence.main, [str(a) for a in args])
    return code, printed.getvalue()


def line(result, fingerprint="now1", name=None, basis=None, time="T1", kind="K"):
    d = {"time": time, "kind": kind, "fingerprint": fingerprint, "result": result}
    if name is not None:
        d["name"] = name
    if basis is not None:
        d["basis"] = basis
    return d


# ───────── evidence.py: sections of the spec text ─────────
print("evidence.py — sections, numbered items, the report and its fingerprint")
TEXT = "# Spec\n\n## Expected results\n- E1 first\n* E2 second\n\n## Results\nnotes\n### Verification Report\nverdict: pass\nfingerprint abc123def456 more\n### Next\nfingerprint zzz\n\n## Later\ntail\n"
check("section: the body runs up to the next `## ` line", evidence.section(TEXT, "Expected results") == "- E1 first\n* E2 second\n\n", evidence.section(TEXT, "Expected results"))
check("section: a missing heading is None (also for None text)", evidence.section(TEXT, "Approval") is None and evidence.section(None, "Results") is None)
check("section: the heading is matched whatever its letter case, for every caller (`## Expected Results` · `## approval` · `## TOOLS` · `## finish line` were not found)",
      [evidence.section("## Expected Results\n- E1 x\n", "Expected results"), evidence.section("intro\n## approval\nq\n## Next\n", "Approval"),
       evidence.section("## TOOLS\nt\n", "Tools"), evidence.section("## finish line\nf\n", "Finish line")] == ["- E1 x\n", "q\n", "t\n", "f\n"],
      [evidence.section("## Expected Results\n- E1 x\n", "Expected results"), evidence.section("intro\n## approval\nq\n## Next\n", "Approval")])
check("section: the heading must still match from its start (`## My results` is not `Results` · `### Results` is not a `## ` heading)",
      evidence.section("## My results\nx\n", "Results") is None and evidence.section("### Results\nx\n", "Results") is None)
check("has_numbered_items: the items under `## Expected Results` (capital R) count — they used to drop out, and with them the traceability check",
      evidence.has_numbered_items("## Expected Results\n- E1 first\n") is True)
check("report_section: the report under `## results` (small r) is found", evidence.report_section("## results\n### Verification Report\nfingerprint low\n") == "fingerprint low\n")
check("has_numbered_items: E-numbered bullets count", evidence.has_numbered_items(TEXT) is True)
check("has_numbered_items: a section without E-numbers is False", evidence.has_numbered_items("## Expected results\n- plain line\n- Extra words\n") is False)
check("has_numbered_items: no such section is False", evidence.has_numbered_items("## Other\n- E1 x\n") is False)
check("report_section: from the heading inside Results up to the next ### line", evidence.report_section(TEXT) == "verdict: pass\nfingerprint abc123def456 more\n", evidence.report_section(TEXT))
check("report_section: a report heading before `## Results` is not the report",
      evidence.report_section("### Verification Report\nfingerprint fake\n\n## Results\nnothing\n") is None)
check("report_section: a text without `## Results` is searched as a whole", evidence.report_section("intro\n### Verification Report\nfingerprint whole\n") == "fingerprint whole\n")
check("recorded_fingerprint: the word after `fingerprint` in the report", evidence.recorded_fingerprint(TEXT) == "abc123def456", evidence.recorded_fingerprint(TEXT))
check("recorded_fingerprint: None without a report · None when the report has no fingerprint",
      evidence.recorded_fingerprint("## Results\nfingerprint outside\n") is None and evidence.recorded_fingerprint("## Results\n### Verification Report\nverdict\n") is None)

# ───────── evidence.py: fingerprint ─────────
print("evidence.py — fingerprint")
repo = new_repo({"calc.py": "VALUE = 1\n"})
plain = new_folder({"calc.py": "VALUE = 1\n"})
first = evidence.fingerprint(repo)
check("in a git repository: 12 hex characters", isinstance(first, str) and len(first) == 12 and all(c in "0123456789abcdef" for c in first), first)
check("outside git: None (unknown), not an empty text", evidence.fingerprint(plain) is None, evidence.fingerprint(plain))
(repo / ".oqa").mkdir()
(repo / ".oqa" / "note.txt").write_text("state\n", encoding="utf-8")
check("a file under .oqa/ does not change the fingerprint", evidence.fingerprint(repo) == first, evidence.fingerprint(repo))
(repo / "calc.py").write_text("VALUE = 2\n", encoding="utf-8")
changed = evidence.fingerprint(repo)
check("a changed code file changes it", changed is not None and changed != first, (first, changed))
(repo / "calc.py").write_text("VALUE = 1\n", encoding="utf-8")
check("the same content gives the same fingerprint again", evidence.fingerprint(repo) == first)

# a tracked file that .gitignore names is code too — `git add -A` into an empty index passes over it
ignored = new_repo({"calc.py": "VALUE = 1\n", ".gitignore": "settings.py\nscratch/\n", ".oqa/tests.json": "[]\n"})
(ignored / "settings.py").write_text("DEBUG = False\n", encoding="utf-8")
git(ignored, "add", "-f", "settings.py")
git(ignored, "commit", "-qm", "settings.py is tracked although .gitignore names it")
seeded = evidence.fingerprint(ignored)
(ignored / "settings.py").write_text("DEBUG = True\n", encoding="utf-8")
edited = evidence.fingerprint(ignored)
check("a tracked file that .gitignore names (force-added `settings.py`) is in the fingerprint: editing it changes the fingerprint — it used to stay the same, so evidence for the old content stayed valid",
      seeded is not None and edited is not None and edited != seeded, (seeded, edited))
(ignored / "settings.py").write_text("DEBUG = False\n", encoding="utf-8")
check("… and the first content gives the first fingerprint again", evidence.fingerprint(ignored) == seeded, (seeded, evidence.fingerprint(ignored)))
(ignored / "scratch").mkdir()
(ignored / "scratch" / "notes.py").write_text("x = 1\n", encoding="utf-8")
check("an ignored file that is not tracked stays outside the fingerprint", evidence.fingerprint(ignored) == seeded, (seeded, evidence.fingerprint(ignored)))
(ignored / ".oqa" / "tests.json").write_text("[{\"name\": \"a\", \"command\": \"python a.py\"}]\n", encoding="utf-8")
check("a committed file under .oqa/ is still left out when the index starts from HEAD (editing it does not change the fingerprint)", evidence.fingerprint(ignored) == seeded, (seeded, evidence.fingerprint(ignored)))
(ignored / "settings.py").unlink()
check("deleting the tracked ignored file changes the fingerprint too", evidence.fingerprint(ignored) not in (None, seeded), evidence.fingerprint(ignored))
fresh = new_folder({"calc.py": "VALUE = 1\n"})
git(fresh, "init", "-q")
no_commit = evidence.fingerprint(fresh)
(fresh / "calc.py").write_text("VALUE = 2\n", encoding="utf-8")
check("a repository without any commit (no HEAD to start the index from) is still measured, and the fingerprint follows the content",
      isinstance(no_commit, str) and len(no_commit) == 12 and evidence.fingerprint(fresh) not in (None, no_commit), (no_commit, evidence.fingerprint(fresh)))


class _Done:
    def __init__(self, returncode, stdout):
        self.returncode, self.stdout, self.stderr = returncode, stdout, ""


def fingerprint_with_write_tree(returncode, stdout):
    """fingerprint() with git replaced: add and rm succeed, write-tree answers as given"""
    real = subprocess.run
    def fake(command, **_kwargs):
        return _Done(returncode, stdout) if "write-tree" in command else _Done(0, "")
    evidence.subprocess.run = fake
    try:
        return call(evidence.fingerprint, plain)
    finally:
        evidence.subprocess.run = real


got = fingerprint_with_write_tree(0, "0123456789abcdef0123\n")
check("write-tree exit 0 with a hash: the first 12 characters", got == "0123456789ab", got)
got = fingerprint_with_write_tree(0, "  \n")
check("write-tree exit 0 but no output: None (both exit 0 AND a hash are needed)", got is None, repr(got))
got = fingerprint_with_write_tree(1, "0123456789abcdef0123\n")
check("write-tree exit 1 with output: None (output of a failed call is not a fingerprint)", got is None, repr(got))


def fingerprint_timeouts(setting, *args):
    """The timeout each git call of fingerprint() was given, with OQA_FINGERPRINT_TIMEOUT set to `setting` (None: not set)"""
    real, seen, before = subprocess.run, [], os.environ.pop("OQA_FINGERPRINT_TIMEOUT", None)
    def fake(command, **kwargs):
        seen.append(kwargs.get("timeout"))
        return _Done(0, "0123456789abcdef0123\n")
    if setting is not None:
        os.environ["OQA_FINGERPRINT_TIMEOUT"] = setting
    evidence.subprocess.run = fake
    try:
        call(evidence.fingerprint, plain, *args)
    finally:
        evidence.subprocess.run = real
        os.environ.pop("OQA_FINGERPRINT_TIMEOUT", None)
        if before is not None:
            os.environ["OQA_FINGERPRINT_TIMEOUT"] = before
    return seen


# five git calls since the index starts from HEAD: is there a HEAD · read its tree · add everything · drop .oqa · write the tree (it used to be three)
check("each git call of the fingerprint gets 60 seconds by default (8 seconds left large repositories permanently unknown) — five calls, the index is seeded from HEAD first", fingerprint_timeouts(None) == [60] * 5, fingerprint_timeouts(None))
check("OQA_FINGERPRINT_TIMEOUT sets the seconds", fingerprint_timeouts("240") == [240] * 5 and fingerprint_timeouts("0.5") == [0.5] * 5, (fingerprint_timeouts("240"), fingerprint_timeouts("0.5")))
check("a bad OQA_FINGERPRINT_TIMEOUT (not a number · zero · negative · empty · nan · inf) falls back to 60",
      [fingerprint_timeouts(bad) for bad in ("soon", "0", "-5", "", "nan", "inf")] == [[60] * 5] * 6, [fingerprint_timeouts(bad) for bad in ("soon", "0", "-5", "", "nan", "inf")])
check("a timeout given by the caller wins over the environment", fingerprint_timeouts("240", 3) == [3] * 5, fingerprint_timeouts("240", 3))


def fingerprint_with_failing(word):
    """fingerprint() of a real repository while the git call that carries `word` exits 1"""
    real = subprocess.run
    def fake(command, **kwargs):
        return _Done(1, "") if word in command else real(command, **kwargs)
    evidence.subprocess.run = fake
    try:
        return call(evidence.fingerprint, ignored)
    finally:
        evidence.subprocess.run = real


check("when the tree of HEAD cannot be read into the index the fingerprint is None (unknown) — not the fingerprint of an index that lacks the tracked ignored files",
      fingerprint_with_failing("read-tree") is None and fingerprint_with_failing("no-such-word") is not None, (fingerprint_with_failing("read-tree"), fingerprint_with_failing("no-such-word")))

# ───────── evidence.py: the spec hash and the one definition of "plain text file" ─────────
print("evidence.py — spec_hash · is_text_file")
SPEC_A = "# Spec\n\n## Expected results\n- E1 first\n\n## Finish line\n- Regression Test\n\n## Approval\n- \"go\"\n\n## Results\nnotes\n"
hash_of = lambda text: call(lambda: evidence.spec_hash(text))      # noqa: E731
check("spec_hash: a short text, the same for the same spec", isinstance(hash_of(SPEC_A), str) and 8 <= len(hash_of(SPEC_A)) <= 16 and hash_of(SPEC_A) == hash_of(SPEC_A), hash_of(SPEC_A))
check("spec_hash: it changes when an expected result is added, and when the finish line changes",
      len({hash_of(SPEC_A), hash_of(SPEC_A.replace("- E1 first\n", "- E1 first\n- E2 second\n")), hash_of(SPEC_A.replace("- Regression Test\n", "- Regression Test · Mutation Test\n"))}) == 3,
      (hash_of(SPEC_A), hash_of(SPEC_A.replace("- E1 first\n", "- E1 first\n- E2 second\n"))))
check("spec_hash: it does not change when other sections change (the report written under Results · the approval) or with CRLF line ends",
      hash_of(SPEC_A.replace("notes\n", "notes\n### Verification Report\nx\n").replace("\"go\"", "\"go ahead\"")) == hash_of(SPEC_A) == hash_of(SPEC_A.replace("\n", "\r\n")), hash_of(SPEC_A.replace("\n", "\r\n")))
check("spec_hash: a spec without the finish-line section differs from one whose section is empty (missing is not the same as empty)",
      hash_of("## Expected results\n- E1 x\n") != hash_of("## Expected results\n- E1 x\n\n## Finish line\n"), hash_of("## Expected results\n- E1 x\n"))
text_file = lambda name: call(lambda: evidence.is_text_file(name))      # noqa: E731
check("is_text_file: known text suffixes (any letter case) and names are text", [text_file(n) for n in ("notes.md", "doc/A.TXT", "x.rst", "img/Logo.PNG", "LICENSE", "sub/.gitattributes", ".editorconfig")] == [True] * 7,
      [text_file(n) for n in ("notes.md", "doc/A.TXT", "x.rst", "img/Logo.PNG", "LICENSE", "sub/.gitattributes", ".editorconfig")])
NOT_TEXT = (".gitignore", "sub/.gitignore", "requirements.txt", "requirements-dev.txt", "deps/Requirements_test.TXT", "constraints.txt", "constraints-3.12.txt", "app.js", "Dockerfile", "data.json")
check("is_text_file: `.gitignore` is not plain text (a changed .gitignore can hide a new code file from every tool) · `requirements*.txt` and `constraints*.txt` are not (they decide which code runs) — rule changed for all three",
      [text_file(n) for n in NOT_TEXT] == [False] * len(NOT_TEXT), list(zip(NOT_TEXT, [text_file(n) for n in NOT_TEXT])))

# ───────── evidence.py: check_evidence ─────────
print("evidence.py — check_evidence: state and basis")
old = line("pass", fingerprint="old1")
got = call(evidence.check_evidence, [old], "K", "now1")
check("only evidence for other code: stale · the basis names both fingerprints · no current lines",
      got == ("stale", "T1 pass · fingerprint old1 (now now1) — evidence for different code", []), got)
got = call(evidence.check_evidence, [old], "K", None)
check("fingerprint now unknown: stale and the basis says `now unknown`",
      got == ("stale", "T1 pass · fingerprint old1 (now unknown) — evidence for different code", []), got)
got = call(evidence.check_evidence, [line("pass", fingerprint=None)], "K", None)
check("a line without fingerprint does not match an unknown fingerprint now", got[0] == "stale", got)
got = call(evidence.check_evidence, [line("pass", kind="Other")], "K", "now1")
check("no line of this kind: missing", got == ("missing", "no evidence recorded for this check", []), got)

whole = line("fail", basis="2 tests failed")
got = call(evidence.check_evidence, [whole], "K", "now1")
check("whole-check failure: failed · the basis ends with the recorded basis", got == ("failed", "T1 fail · 2 tests failed", [whole]), got)
whole = line("rejected")
got = call(evidence.check_evidence, [whole], "K", "now1")
check("whole-check failure without a basis: the basis part is empty (not None)", got == ("failed", "T1 rejected · ", [whole]), got)

lines = [line("pass", basis="all"), line("fail", name="t1", basis="boom", time="T2")]
got = call(evidence.check_evidence, lines, "K", "now1")
check("an individual failure recorded after the whole pass: failed · named in the basis", got == ("failed", "T2 t1 fail · boom", lines), got)
lines = [line("fail", name="t1", basis="boom"), line("pass", basis="all", time="T2")]
got = call(evidence.check_evidence, lines, "K", "now1")
check("an individual failure recorded before the whole pass is covered by it: ok", got == ("ok", "T2 pass · all", lines), got)
lines = [line("pass", basis="all"), line("fail", name="t1", basis="a", time="T2"), line("fail", name="t2", basis="b", time="T3"), line("pass", name="t2", time="T4")]
got = call(evidence.check_evidence, lines, "K", "now1")
check("the latest result per name counts: t2 passed later, t1 still fails", got == ("failed", "T2 t1 fail · a", lines), got)

whole = line("unknown", basis="tool not installed")
got = call(evidence.check_evidence, [whole], "K", "now1")
check("whole-check unknown: unknown · the basis keeps the reason", got == ("unknown", "T1 unknown · tool not installed", [whole]), got)
whole = line("unknown")
got = call(evidence.check_evidence, [whole], "K", "now1")
check("whole-check unknown without a basis: the basis part is empty", got == ("unknown", "T1 unknown · ", [whole]), got)

whole = line("approved", basis="reviewed 5")
got = call(evidence.check_evidence, [whole], "K", "now1")
check("whole-check success: ok · the basis keeps the recorded basis", got == ("ok", "T1 approved · reviewed 5", [whole]), got)
whole = line("done")
got = call(evidence.check_evidence, [whole], "K", "now1")
check("whole-check success without a basis: the basis part is empty", got == ("ok", "T1 done · ", [whole]), got)
lines = [line("fail", basis="first"), line("pass", basis="second", time="T2")]
got = call(evidence.check_evidence, lines, "K", "now1")
check("the latest whole-check line is the result", got == ("ok", "T2 pass · second", lines), got)

lines = [line("pass", name="a"), line("fail", name="b", time="T2")]
got = call(evidence.check_evidence, lines, "K", "now1")
check("named lines only, one bad: failed · no `and N more`", got == ("failed", "T2 b fail", lines), got)
lines = [line("fail", name="a"), line("rejected", name="b", time="T2"), line("fail", name="c", time="T3")]
got = call(evidence.check_evidence, lines, "K", "now1")
check("named lines only, three bad: the last one and `and 2 more`", got == ("failed", "T3 c fail and 2 more", lines), got)
lines = [line("pass", name="a"), line("pass", name="b", time="T2"), line("done", name="c", time="T3")]
got = call(evidence.check_evidence, lines, "K", "now1")
check("named lines only, every one good: ok · counts the good ones", got == ("ok", "T3 c and others, 3 in all, done", lines), got)
lines = [line("pass", name="a"), line("unknown", name="b", time="T2"), line("done", name="c", time="T3")]
got = call(evidence.check_evidence, lines, "K", "now1")
check("named lines only, none bad but one unknown: unknown, naming it — the pass of another name does not cover a result nobody knows (it used to be ok)",
      got == ("unknown", "T2 b unknown — its result is not known, and the pass of another name does not cover it", lines), got)
lines = [line("pass", name="a"), line("unknown", name="b", time="T2"), line("pass", name="b", time="T3")]
got = call(evidence.check_evidence, lines, "K", "now1")
check("… the same name run again with a good result lifts it (the latest result per name)", got == ("ok", "T3 b and others, 2 in all, pass", lines), got)
lines = [line("pass", basis="all"), line("unknown", name="t1", time="T2")]
got = call(evidence.check_evidence, lines, "K", "now1")
check("an individual unknown recorded after the whole pass: unknown (the whole pass was recorded before that run)",
      got == ("unknown", "T2 t1 unknown — recorded after the whole-check line, its result is not known", lines), got)
lines = [line("unknown", name="t1"), line("pass", basis="all", time="T2")]
got = call(evidence.check_evidence, lines, "K", "now1")
check("an individual unknown recorded before the whole pass is covered by it: ok", got == ("ok", "T2 pass · all", lines), got)
whole = line("skipped", basis="not run")
got = call(evidence.check_evidence, [whole], "K", "now1")
check("a whole-check line with a result that is not one of the known words (\"skipped\"): unknown, not ok — only pass · approved · done are good",
      got == ("unknown", "T1 result 'skipped' is not a known result — taken as unknown · not run", [whole]), got)
whole = {"time": "T1", "kind": "K", "fingerprint": "now1"}
got = call(evidence.check_evidence, [whole], "K", "now1")
check("a whole-check line without a result: unknown, not ok", got == ("unknown", "T1 result None is not a known result — taken as unknown · ", [whole]), got)
lines = [line("pass", name="a"), line("skipped", name="b", time="T2")]
got = call(evidence.check_evidence, lines, "K", "now1")
check("a named line with an unrecognised result is not known either", got == ("unknown", "T2 b skipped — its result is not known, and the pass of another name does not cover it", lines), got)
lines = [line("unknown", name="a"), line("unknown", name="b", time="T2")]
got = call(evidence.check_evidence, lines, "K", "now1")
check("named lines only, every result unknown: unknown with its own basis and the current lines",
      got == ("unknown", "there are lines with the fingerprint now, but every result is unknown", lines), got)
lines = [line("pass", fingerprint="old1", basis="old"), line("fail", basis="new")]
got = call(evidence.check_evidence, lines, "K", "now1")
check("lines for other code are not evidence and not in the returned lines", got == ("failed", "T1 fail · new", [lines[1]]), got)

# ───────── evidence.py: record and read ─────────
print("evidence.py — record · record_quietly · read")
rec = new_repo({"calc.py": "VALUE = 1\n"})
fp = evidence.fingerprint(rec)
written = call(evidence.record, rec, "Unit Test", "pass", "x" * 500, None, "tool-a", name="t1", equivalent=None)
check("record: measures the fingerprint itself · cuts the basis at 400 · keeps extra keys that are not None",
      isinstance(written, dict) and written.get("fingerprint") == fp and len(written.get("basis", "")) == 400 and written.get("name") == "t1"
      and "equivalent" not in written and written.get("tool") == "tool-a" and written.get("kind") == "Unit Test", written)
check("recording does not change the fingerprint (.oqa/ is outside it)", evidence.fingerprint(rec) == fp)
written = call(evidence.record, rec, "Unit Test", "fail", None, "given1")
check("record: a given fingerprint is used as it is · a None basis is empty text", isinstance(written, dict) and written.get("fingerprint") == "given1" and written.get("basis") == "", written)
got = call(evidence.record, rec, "Unit Test", "passed")
check("record: a result outside the fixed values is refused (ValueError)", isinstance(got, tuple) and got[0] == "raised" and "ValueError" in got[1], got)
check("record_quietly: the same refusal is None instead of an exception", call(evidence.record_quietly, rec, "Unit Test", "passed") is None)
quiet = call(evidence.record_quietly, rec, "Unit Test", "unknown", "why")
check("record_quietly: a valid line is written and returned", isinstance(quiet, dict) and quiet.get("result") == "unknown" and quiet.get("fingerprint") == fp, quiet)
with open(evidence.evidence_path(rec), "a", encoding="utf-8") as f:
    f.write("not json\n[1, 2]\n\n")
got = evidence.read(rec)
check("read: the three recorded lines in order · broken and non-dict lines are skipped", [d.get("result") for d in got] == ["pass", "fail", "unknown"], got)
check("read: no file is an empty list", evidence.read(plain) == [])
check("damaged: counts the non-empty lines that are not JSON objects (`not json` and `[1, 2]` — the empty line is not damage)", call(lambda: evidence.damaged(rec)) == 2, call(lambda: evidence.damaged(rec)))
check("damaged: no file is 0 · a file of whole lines is 0", call(lambda: evidence.damaged(plain)) == 0 and call(lambda: evidence.damaged(new_folder({".oqa/evidence.jsonl": "{\"kind\": \"a\"}\n\n   \n"}))) == 0)
written = call(evidence.record, plain, "Unit Test", "pass")
check("record outside git: the fingerprint is the word unknown", isinstance(written, dict) and written.get("fingerprint") == "unknown", written)

# ───────── evidence.py: CLI ─────────
print("evidence.py — CLI: exit codes and what is printed")
cli = new_repo({"calc.py": "VALUE = 1\n"})
nogit = new_folder({"calc.py": "VALUE = 1\n"})
fp = evidence.fingerprint(cli)
code, out = run_cli("fingerprint", "--repo", cli)
check("fingerprint in a repository: exit code 0 · prints the fingerprint", code == 0 and out.strip() == fp, (code, out))
code, out = run_cli("fingerprint", "--repo", nogit)
check("fingerprint outside git: exit code 2 · prints unknown", code == 2 and out.strip() == "unknown", (code, out))
code, out = run_main("fingerprint", "--repo", cli)
check("main(fingerprint) returns the number 0 when measured", code == 0 and code is not None and out.strip() == fp, (code, out))
code, out = run_main("fingerprint", "--repo", nogit)
check("main(fingerprint) returns 2 when it cannot be measured", code == 2 and out.strip() == "unknown", (code, out))

code, out = run_main("record", "--repo", cli, "--kind", "Mutation Test")
check("record with --kind but no --result: returns 2 · says what it needs · writes nothing",
      code == 2 and out.strip() == "record needs --kind and --result" and not evidence.evidence_path(cli).exists(), (code, out))
code, out = run_main("record", "--repo", cli, "--result", "pass")
check("record with --result but no --kind: returns 2 · says what it needs · writes nothing",
      code == 2 and out.strip() == "record needs --kind and --result" and not evidence.evidence_path(cli).exists(), (code, out))
code, out = run_cli("record", "--repo", cli, "--kind", "Mutation Test")
check("the same through the command line: exit code 2", code == 2 and "record needs --kind and --result" in out, (code, out))
KINDS = ("Regression Test", "Mutation Test", "Manual Mutation Test", "Requirements Traceability", "E2E UI Test", "Silent Failure Review", "Reasoning Review",
         "Blind Review", "Fuzz Test (LLM)", "Independent Review Gate", "Baseline", "Verification Report")
check("KNOWN_KINDS: the eleven check names of the rule table and the verification report", call(lambda: tuple(evidence.KNOWN_KINDS)) == KINDS, call(lambda: evidence.KNOWN_KINDS))
code, out = run_main("record", "--repo", cli, "--kind", "Mutaton Test", "--result", "pass")
check("a --kind that is not a known check name (a typo) is refused: returns 2 · names the word and lists the valid names · writes nothing (it used to return 0 and the real check stayed missing)",
      code == 2 and out.strip() == "unknown check name 'Mutaton Test' — --kind must be one of: " + " · ".join(KINDS) and not evidence.evidence_path(cli).exists(), (code, out))
code, out = run_cli("record", "--repo", cli, "--kind", "silent failure review", "--result", "done", "--fingerprint", "start123")
check("the same through the command line (the name in other letter case is not the name): exit code 2 · writes nothing",
      code == 2 and "--kind must be one of: Regression Test" in out and not evidence.evidence_path(cli).exists(), (code, out))
code, out = run_main("record", "--repo", cli, "--kind", "Blind Review", "--result", "done")
check("a manual check without --fingerprint: returns 2 · says it needs one · writes nothing",
      code == 2 and out.startswith("\"Blind Review\" needs --fingerprint") and not evidence.evidence_path(cli).exists(), (code, out))
code, out = run_main("record", "--repo", cli, "--kind", "Blind Review", "--result", "done", "--fingerprint", "start123", "--basis", "findings 0")
check("a manual check with --fingerprint: returns the number 0 · prints what was recorded",
      code == 0 and code is not None and out.strip() == "recorded: Blind Review · done · fingerprint start123", (code, out))
# The command line records only what a person (the main conversation) can vouch for: the manual checks and the independent review gate.
# The kinds a tool produces are refused — six hand-typed `pass` lines used to give DONE with nothing run
TOOL_KINDS = {"Regression Test": "report.py", "Mutation Test": "report.py", "Requirements Traceability": "report.py", "Verification Report": "report.py",
              "Manual Mutation Test": "manual_mutate.py", "Baseline": "baseline.py"}
refused = {kind: run_main("record", "--repo", cli, "--kind", kind, "--result", "pass", "--fingerprint", fp, "--basis", "typed by hand") for kind in TOOL_KINDS}
check("a kind that a tool records (Regression · Mutation · Traceability · Verification Report · Manual Mutation · Baseline) is refused on the command line: returns 2 · names the tool that records it · writes nothing"
      " — rule changed: `record --kind \"Mutation Test\" --result pass` used to return 0",
      all(code == 2 and ("is recorded by %s" % TOOL_KINDS[kind]) in out and out.startswith("\"%s\"" % kind) for kind, (code, out) in refused.items())
      and [d.get("kind") for d in evidence.read(cli)] == ["Blind Review"], (refused, evidence.read(cli)))
code, out = run_cli("record", "--repo", cli, "--kind", "Mutation Test", "--result", "pass")
check("the same through the command line, without --fingerprint: exit code 2 (it used to be 0 and record the fingerprint now)", code == 2 and "is recorded by report.py" in out and len(evidence.read(cli)) == 1, (code, out))
code, out = run_cli("record", "--repo", cli, "--kind", "Verification Report", "--result", "unknown")
check("the verification report cannot be typed by hand either, whatever the result: exit code 2", code == 2 and "is recorded by report.py" in out and len(evidence.read(cli)) == 1, (code, out))
check("HAND_KINDS (the manual checks and the independent review gate) and the tool kinds together are exactly KNOWN_KINDS",
      call(lambda: tuple(evidence.HAND_KINDS)) == tuple(evidence.MANUAL_CHECKS) + ("Independent Review Gate",)
      and call(lambda: set(evidence.HAND_KINDS) | set(evidence.TOOL_KINDS)) == set(KINDS) and call(lambda: dict(evidence.TOOL_KINDS)) == TOOL_KINDS,
      (call(lambda: evidence.HAND_KINDS), call(lambda: evidence.TOOL_KINDS)))
code, out = run_main("record", "--repo", cli, "--kind", "Independent Review Gate", "--result", "approved")
check("the independent review gate is recorded by hand, and needs --fingerprint like the manual checks (the value measured when the bundle was made): without it returns 2 · writes nothing",
      code == 2 and out.startswith("\"Independent Review Gate\" needs --fingerprint") and len(evidence.read(cli)) == 1, (code, out))
code, out = run_main("record", "--repo", cli, "--kind", "Independent Review Gate", "--result", "approved", "--fingerprint", fp, "--basis", "round 1")
check("… with --fingerprint: returns 0 · prints what was recorded", code == 0 and out.strip() == "recorded: Independent Review Gate · approved · fingerprint %s" % fp, (code, out))
unmeasured = [run_main("record", "--repo", cli, "--kind", "Blind Review", "--result", "done", "--fingerprint", bad) for bad in ("unknown", "Unknown", "  ")]
check("a fingerprint that could not be measured (the word `unknown` that `evidence.py fingerprint` prints then · blanks) is refused: returns 2 · says so · writes nothing"
      " — it used to write a line that is evidence for no code at all and return 0",
      all(code == 2 and "could not be measured" in out for code, out in unmeasured) and len(evidence.read(cli)) == 2, (unmeasured, evidence.read(cli)))
stored = evidence.read(cli)
check("both hand-recorded lines are in the file, written by the manual tool", [(d.get("kind"), d.get("fingerprint"), d.get("tool"), d.get("basis")) for d in stored]
      == [("Blind Review", "start123", "manual", "findings 0"), ("Independent Review Gate", fp, "manual", "round 1")], stored)
by_tool = [call(evidence.record, cli, "Mutation Test", "pass", "3 passed", None, "report.py"), call(evidence.record_quietly, cli, "Verification Report", "unknown", "", None, "report.py")]
check("the Python functions record · record_quietly still take every kind and measure the fingerprint themselves (the tools call them)",
      [(d.get("kind"), d.get("fingerprint"), d.get("tool")) if isinstance(d, dict) else d for d in by_tool] == [("Mutation Test", fp, "report.py"), ("Verification Report", fp, "report.py")], by_tool)

code, out = run_main("show", "--repo", cli)
shown = out.splitlines()
check("show returns the number 0", code == 0 and code is not None, (code, out))
check("show: the first line gives the fingerprint now and the number of lines",
      bool(shown) and shown[0].startswith("fingerprint now %s · evidence 4 lines · " % fp) and shown[0].endswith("evidence.jsonl"), out)
check("show: one line per kind in name order — the manual check is stale, the gate and the mutation test are ok, the report is unknown",
      len(shown) == 5 and shown[1].split()[:3] == ["Blind", "Review", "stale"] and "(now %s)" % fp in shown[1] and shown[2].split()[:4] == ["Independent", "Review", "Gate", "ok"]
      and shown[3].split()[:3] == ["Mutation", "Test", "ok"] and shown[4].split()[:3] == ["Verification", "Report", "unknown"], out)
code, out = run_main("show", "--repo", nogit)
check("show outside git: returns 0 · the fingerprint now is the word unknown · no evidence",
      code == 0 and code is not None and out.startswith("fingerprint now unknown · evidence 0 lines · ") and len(out.splitlines()) == 1, (code, out))
code, out = run_cli("show", "--repo", cli)
check("show through the command line: exit code 0 and the same first line", code == 0 and out.startswith("fingerprint now %s · evidence 4 lines" % fp), (code, out))

# ───────── spec.py: read_text and current_spec ─────────
print("spec.py — read_text · current_spec")
SPEC = "# Spec\n"
base = new_folder()
check("read_text: a missing file is None", spec.read_text(base / "missing.md") is None)
check("read_text: a folder is None (unreadable, not a crash)", call(spec.read_text, base) is None, call(spec.read_text, base))
(base / "bom.md").write_bytes(b"\xef\xbb\xbfhello \xff")
check("read_text: a BOM is dropped and a bad byte becomes a replacement character", spec.read_text(base / "bom.md") == "hello �", repr(spec.read_text(base / "bom.md")))

check("no .oqa/specs folder: None", call(spec.current_spec, base) is None)
(base / ".oqa").mkdir()
(base / ".oqa" / "specs").write_text("a file, not a folder\n", encoding="utf-8")
check(".oqa/specs that is a file: None", call(spec.current_spec, base) is None)

two = new_folder({".oqa/specs/2026-01-01_a.md": SPEC, ".oqa/specs/2026-02-02_b.md": SPEC, ".oqa/specs/notes.txt": "x\n"})
specs = two / ".oqa" / "specs"
older, latest = (specs / "2026-01-01_a.md").resolve(), (specs / "2026-02-02_b.md").resolve()
problem = lambda root: call(lambda: spec.current_problem(root))      # noqa: E731
NOT_A_SPEC = ".oqa/current names \"%s\", which is not a spec file (.md) directly inside .oqa/specs (a missing file, a template, not a .md file or a path outside the folder) — correct it to name the spec to use"
SEVERAL = ".oqa/current is missing or empty and .oqa/specs holds %d spec files (%s), so the current spec is unknown — write the file name of the spec to use into .oqa/current"
BOTH = SEVERAL % (2, "2026-01-01_a.md, 2026-02-02_b.md")
got = call(spec.current_spec, two)
check("no .oqa/current and two specs: None — the rule changed: the file with the latest name is no longer taken (`draft.md` or `README.md` used to beat a dated spec)", got is None, got)
check("current_problem then says that .oqa/current must name the spec to use and lists the candidates (the .txt file is not one)", problem(two) == BOTH and problem(str(two)) == BOTH, problem(two))
(two / ".oqa" / "current").write_text("2026-01-01_a.md\n", encoding="utf-8")
got = call(spec.current_spec, two)
check(".oqa/current names the older file: that one, not the latest", isinstance(got, Path) and got == older, got)
check("the argument may be a text path", isinstance(got, Path) and call(spec.current_spec, str(two)) == got)
check("current_problem: None while .oqa/current names an existing spec", problem(two) is None and problem(str(two)) is None, problem(two))
(two / ".oqa" / "current").write_text("no_such.md\n", encoding="utf-8")
got = call(spec.current_spec, two)
check(".oqa/current names a missing file (a typo): None — it does not silently switch to another spec (the work would be judged against another spec's approval)", got is None, got)
check("current_problem: one line that names the bad value (wording changed: it no longer offers \"the spec with the latest name\")", problem(two) == NOT_A_SPEC % "no_such.md", problem(two))
(two / ".oqa" / "current").write_text("  \n", encoding="utf-8")
got = call(spec.current_spec, two)
check(".oqa/current that is blank, two specs: None (rule changed — it used to be the latest name)", got is None, got)
check("current_problem for a blank .oqa/current with two specs: the candidates line (rule changed — it used to be None)", problem(two) == BOTH, problem(two))
os.remove(two / ".oqa" / "current")
check("current_problem without .oqa/current, two specs: the candidates line (rule changed — it used to be None)", problem(two) == BOTH, problem(two))
(two / ".oqa" / "current").mkdir()
got = call(spec.current_spec, two)
check(".oqa/current that is there but cannot be read (a folder): None — what it names is unknown, so no other spec is taken in its place", got is None, got)
check("current_problem: says that .oqa/current cannot be read (wording changed: no \"latest name\")",
      problem(two) == ".oqa/current is there but cannot be read, so the current spec is unknown — write the file name of the spec to use into it", problem(two))
os.rmdir(two / ".oqa" / "current")

outside_spec = two / "notes.md"
outside_spec.write_text(SPEC, encoding="utf-8")
(specs / "deeper").mkdir()
(specs / "deeper" / "inner.md").write_text(SPEC, encoding="utf-8")
for bad in ("../../notes.md", str(outside_spec.resolve()), "deeper/inner.md", "deeper", "2026-02-02_b.md\n2026-01-01_a.md"):
    (two / ".oqa" / "current").write_text(bad + "\n", encoding="utf-8")
    got = call(spec.current_spec, two)
    check(".oqa/current names %r (outside the folder · a subfolder · a folder · two lines): None, and current_problem names the value" % bad[-22:],
          got is None and problem(two) == NOT_A_SPEC % " ".join(bad.split()), (got, problem(two)))
(two / ".oqa" / "current").write_text("notes.txt\n", encoding="utf-8")
got = call(spec.current_spec, two)
check(".oqa/current must name a .md file: an existing `notes.txt` inside the folder is not a spec — None, and current_problem names the value",
      got is None and problem(two) == NOT_A_SPEC % "notes.txt", (got, problem(two)))
os.remove(two / ".oqa" / "current")

(specs / "YYYY-MM-DD_name.md").write_text(SPEC, encoding="utf-8")
(specs / "zzz.md").mkdir()
check("a template copied as is (YYYY…) and a folder named `zzz.md` are not candidates: the line still lists the two real specs", problem(two) == BOTH, problem(two))
(two / ".oqa" / "current").write_text("YYYY-MM-DD_name.md\n", encoding="utf-8")
got = call(spec.current_spec, two)
check(".oqa/current names the YYYY template: None — neither the template nor, silently, a real spec", got is None, got)
check("current_problem: names the template value", problem(two) == NOT_A_SPEC % "YYYY-MM-DD_name.md", problem(two))
(two / ".oqa" / "current").write_text("zzz.md\n", encoding="utf-8")
check(".oqa/current names a folder called `zzz.md`: None, and current_problem names the value", call(spec.current_spec, two) is None and problem(two) == NOT_A_SPEC % "zzz.md", problem(two))
os.remove(two / ".oqa" / "current")
check("current_problem: None when there is no specs folder at all (that is the plain \"no spec\" case)",
      problem(new_folder({".oqa/current": "x.md\n"})) is None, problem(new_folder({".oqa/current": "x.md\n"})))

only_template = new_folder({".oqa/specs/YYYY-MM-DD_name.md": SPEC})
check("only the template: None (and nothing to say — there is no spec)", call(spec.current_spec, only_template) is None and problem(only_template) is None, call(spec.current_spec, only_template))
(only_template / ".oqa" / "current").write_text("YYYY-MM-DD_name.md\n", encoding="utf-8")
check("only the template, named in .oqa/current: still None", call(spec.current_spec, only_template) is None, call(spec.current_spec, only_template))
one = new_folder({".oqa/specs/only.md": SPEC})
only = (one / ".oqa" / "specs" / "only.md").resolve()
got = call(spec.current_spec, one)
check("a single spec and no .oqa/current: that one · nothing to say", isinstance(got, Path) and got.resolve() == only and problem(one) is None, (got, problem(one)))
(one / ".oqa" / "specs" / "YYYY-MM-DD_name.md").write_text(SPEC, encoding="utf-8")
(one / ".oqa" / "specs" / "zzz.md").mkdir()
(one / ".oqa" / "specs" / "notes.txt").write_text("x\n", encoding="utf-8")
got = call(spec.current_spec, one)
check("a single spec next to the template, a folder named `zzz.md` and a .txt file: still that one — only regular .md files count (the folder used to be returned as the spec)",
      isinstance(got, Path) and got.resolve() == only and problem(one) is None, (got, problem(one)))
(one / ".oqa" / "current").write_text("\n", encoding="utf-8")
got = call(spec.current_spec, one)
check("an empty .oqa/current with a single spec: that one", isinstance(got, Path) and got.resolve() == only and problem(one) is None, (got, problem(one)))
drafts = new_folder({".oqa/specs/2026-01-10_new.md": SPEC, ".oqa/specs/draft.md": SPEC, ".oqa/specs/README.md": SPEC})
got = call(spec.current_spec, drafts)
check("a dated spec next to `draft.md` and `README.md`, no .oqa/current: None — no file wins by its name (`draft.md` used to) · the three are listed",
      got is None and problem(drafts) == SEVERAL % (3, "2026-01-10_new.md, README.md, draft.md"), (got, problem(drafts)))
none = new_folder({".oqa/specs/notes.txt": "x\n"})
check("a specs folder without .md files: None", call(spec.current_spec, none) is None and problem(none) is None)
check("spec.py no longer carries the unused constant CODE_SUFFIXES", not hasattr(spec, "CODE_SUFFIXES"))

# ───────── spec.py: approved ─────────
print("spec.py — approved")
place = new_folder()
_count = [0]


def approved_for(text):
    _count[0] += 1
    path = place / ("spec%d.md" % _count[0])
    path.write_text(text, encoding="utf-8")
    return call(spec.approved, path)


check("a missing file: False (a spec that cannot be read is never taken as approved)", call(spec.approved, place / "missing.md") is False, call(spec.approved, place / "missing.md"))
check("a folder: False (cannot be read as a spec — not approved)", call(spec.approved, place) is False, call(spec.approved, place))
check("straight quotes under Approval: True", approved_for("## Approval\n- 2026-01-01 · \"go ahead\"\n") is True)
check("curly quotes under Approval: True", approved_for("## Approval\n- 2026-01-01 · “go ahead”\n") is True)
check("a quote over several lines: True", approved_for("## Approval\n- \"go\nahead\"\n") is True)
check("wording that contains < still counts", approved_for("## Approval\n- \"go ahead without the <div>\"\n") is True)
check("no quoted wording: False", approved_for("## Approval\n- 2026-01-01 · go ahead\n") is False)
check("empty quotes: False", approved_for("## Approval\n- \"\"\n") is False)
check("no Approval section: False", approved_for("## Other\n- \"go ahead\"\n") is False)
check("a quote in the next section does not count", approved_for("## Approval\n- waiting\n\n## Results\n- \"go ahead\"\n") is False)
check("the template's two placeholders on one line: False", approved_for("## Approval\n- <date> · \"<user's words as they were said>\"\n") is False)
check("a line that still has <date> but real quoted words is an approval: True (rule changed — only the words placeholder marks the template line; it used to be False)",
      approved_for("## Approval\n- <date> · \"go ahead\"\n") is True)
check("a real approval whose words contain <date> is not dropped: True", approved_for("## Approval\n- 2026-01-01 · \"replace <date> and go\"\n") is True)
check("the heading in other letter case (`## approval` · `## APPROVAL`): True — it used to read as empty",
      approved_for("## approval\n- 2026-01-01 · \"go ahead\"\n") is True and approved_for("## APPROVAL\n- 2026-01-01 · \"go ahead\"\n") is True)
check("the docstring says the section must hold only the user's approving words", "only the user's approving words" in " ".join((spec.approved.__doc__ or "").split()))
check("a line with only the <user's words placeholder is left out: False", approved_for("## Approval\n- 2026-01-01 · \"<user's words as they were said>\"\n") is False)
check("a placeholder line next to a real approval line: True", approved_for("## Approval\n- <date> · \"<user's words …>\"\n- 2026-01-01 · \"go\"\n") is True)
check("quotes that hold only blanks are not an approval (straight and curly): False",
      approved_for("## Approval\n- 2026-01-01 · \" \"\n") is False and approved_for("## Approval\n- 2026-01-01 · “  ”\n") is False,
      (approved_for("## Approval\n- 2026-01-01 · \" \"\n"), approved_for("## Approval\n- 2026-01-01 · “  ”\n")))
check("blank quotes next to real wording still approve: True", approved_for("## Approval\n- \" \"\n- 2026-01-01 · \"go ahead\"\n") is True)


def approval_problem_for(text):
    _count[0] += 1
    path = place / ("spec%d.md" % _count[0])
    path.write_text(text, encoding="utf-8")
    return call(lambda: spec.approval_problem(path))


NO_QUOTE = "the spec's \"## Approval\" holds no quoted approval — "
check("approval_problem: None for an approved spec", approval_problem_for("## Approval\n- 2026-01-01 · \"go ahead\"\n") is None, approval_problem_for("## Approval\n- 2026-01-01 · \"go ahead\"\n"))
got = approval_problem_for("## Approval\n- <date> · \"<user's words, exactly as they said them>\"   <- without a quoted line here the verdict stays INCOMPLETE\n")
check("approval_problem: a section that holds only the template placeholder says so — it is not called \"empty\" (the line is there; the user's words are not)",
      got == NO_QUOTE + "only the template's placeholder line is there (replace it with the date and the user's own words in double quotes)", got)
got = approval_problem_for("## Approval\n\n## Results\n")
check("approval_problem: a section without any line says the section is empty", got == NO_QUOTE + "the section is empty", got)
got = approval_problem_for("## Approval\n- 2026-01-01 · go ahead\n")
check("approval_problem: text without quotes says the words must be quoted", got == NO_QUOTE + "the user's own approving words must stand in double quotes", got)
got = approval_problem_for("## Other\n- \"go ahead\"\n")
check("approval_problem: no section at all says so", got == NO_QUOTE + "the spec has no such section", got)
got = call(lambda: spec.approval_problem(place / "missing.md"))
check("approval_problem: a spec that cannot be read says so", got == NO_QUOTE + "the spec file cannot be read", got)

# ───────── spec.py: gate_on_quote ─────────
print("spec.py — gate_on_quote")
HEAD = "## Independent review gate\n"
check("on with quoted words: those words, trimmed", spec.gate_on_quote(HEAD + "- Independent review gate: on (2026-01-01 · \" review it \")\n") == "review it")
check("without a bullet and with spaces around the colon", spec.gate_on_quote(HEAD + "Independent review gate : on (\"yes\")\n") == "yes")
check("off: None", spec.gate_on_quote(HEAD + "- Independent review gate: off\n") is None)
check("on, then a later off line: None (the last line decides)",
      spec.gate_on_quote(HEAD + "- Independent review gate: on (\"yes\")\n- Independent review gate: off\n") is None)
check("off, then a later on line: the words", spec.gate_on_quote(HEAD + "- Independent review gate: off\n- Independent review gate: on (\"now yes\")\n") == "now yes")
check("other lines after the last gate line do not matter", spec.gate_on_quote(HEAD + "- Independent review gate: on (\"yes\")\n- a note: off\n") == "yes")
check("on followed by an appended off: None", spec.gate_on_quote(HEAD + "- Independent review gate: on (\"yes\") off\n") is None)
check("negated on: None", spec.gate_on_quote(HEAD + "- Independent review gate: not on (\"yes\")\n") is None)
check("on without quoted words: None", spec.gate_on_quote(HEAD + "- Independent review gate: on (user said yes)\n") is None)
check("on with blank quoted words: None", spec.gate_on_quote(HEAD + "- Independent review gate: on (\"  \")\n") is None)
check("on with curly quotes only: None (only straight quotes mark the words here)", spec.gate_on_quote(HEAD + "- Independent review gate: on (“yes”)\n") is None)
check("the line outside its section: None", spec.gate_on_quote("## Other\n- Independent review gate: on (\"yes\")\n") is None)
check("no gate line in the section · empty text · None: no words (None)",
      spec.gate_on_quote(HEAD + "- nothing here\n") is None and spec.gate_on_quote("") is None and call(spec.gate_on_quote, None) is None)
check("`On` with a capital: the words (the user's switch is not read as off because of letter case)", spec.gate_on_quote(HEAD + "- Independent review gate: On (\"yes\")\n") == "yes",
      spec.gate_on_quote(HEAD + "- Independent review gate: On (\"yes\")\n"))
check("the key in other letter case (`Independent Review Gate:`): the words", spec.gate_on_quote(HEAD + "- Independent Review Gate: on (\"yes\")\n") == "yes",
      spec.gate_on_quote(HEAD + "- Independent Review Gate: on (\"yes\")\n"))
check("the section heading in other letter case (`## Independent Review Gate`): the words",
      spec.gate_on_quote("## Independent Review Gate\n- INDEPENDENT REVIEW GATE: ON (\"yes\")\n") == "yes", spec.gate_on_quote("## Independent Review Gate\n- INDEPENDENT REVIEW GATE: ON (\"yes\")\n"))
check("on with a trailing `<- …` hint: the words — the hint is not part of the answer (it used to make the line unreadable, so the gate was off)",
      spec.gate_on_quote(HEAD + "- Independent review gate: on (2026-01-01 \"yes\")   <- switched on\n") == "yes", spec.gate_on_quote(HEAD + "- Independent review gate: on (2026-01-01 \"yes\")   <- switched on\n"))
check("quoted words inside the hint are not the user's words: None",
      spec.gate_on_quote(HEAD + "- Independent review gate: on (user said so)   <- write `on (<date> \"<user's words>\")`\n") is None)
check("words with brackets or an arrow inside the quotes are kept whole",
      spec.gate_on_quote(HEAD + "- Independent review gate: on (\"yes (please) <- now\")\n") == "yes (please) <- now", spec.gate_on_quote(HEAD + "- Independent review gate: on (\"yes (please) <- now\")\n"))
check("a later `Off` with a capital and a hint overrides an earlier on: None",
      spec.gate_on_quote(HEAD + "- Independent review gate: on (\"yes\")\n- Independent Review Gate: Off   <- switched off again\n") is None)

print("spec.py — gate_state · gate_problem")
state = lambda text: call(lambda: spec.gate_state(text))      # noqa: E731
problem = lambda text: call(lambda: spec.gate_problem(text))      # noqa: E731
GATE = HEAD + "- Independent review gate: "
TEMPLATE_OFF = GATE + "off   <- to switch it on: `on (<date> \"<user's words>\")`\n"
got = [state(GATE + rest + "\n") for rest in ("on (\"yes\")", "On (2026-01-01 \"yes\")   <- switched on", "ON(\"yes\")")]
check("gate_state: on with quoted words is \"on\" (any letter case · with a hint)", got == ["on"] * 3, got)
got = [state(GATE + "off\n"), state(GATE + "Off\n"), state(GATE + "OFF   <- a note\n"), state(TEMPLATE_OFF), state(GATE + "on (\"yes\")\n- Independent review gate: off\n")]
check("gate_state: a clean off is \"off\" (any letter case · the template's line with its hint · a later off line)", got == ["off"] * 5, got)
UNCLEAR = ("on", "on ()", "on (user said yes)", "on (\"  \")", "on (“yes”)", "maybe", "", "not on (\"yes\")", "on (\"yes\") off", "off (\"yes\")", "offline", "on \"yes\"",
           "off on (\"yes\")", "on (x)   <- \"yes\"")
got = [state(GATE + rest + "\n") for rest in UNCLEAR]
check("gate_state: a last gate line that is neither a clean off nor an on with quoted words is \"unclear\" — on without quotes · on () · maybe · empty · negated · `on (…) off` · …",
      got == ["unclear"] * len(UNCLEAR), list(zip(UNCLEAR, got)))
check("… and gate_on_quote is None for every one of them (unclear is never on)", [spec.gate_on_quote(GATE + rest + "\n") for rest in UNCLEAR] == [None] * len(UNCLEAR))
check("gate_state: the last gate line decides — an unclear line after an on is unclear · an on after an unclear line is on",
      state(GATE + "on (\"yes\")\n- Independent review gate: maybe\n") == "unclear" and state(GATE + "maybe\n- Independent review gate: on (\"yes\")\n") == "on")
check("gate_state: None only without a section or without content in it (no section · empty text · None · the heading alone · blank lines · only an HTML comment or a `<-` hint)",
      [state("## Other\n- Independent review gate: on (\"yes\")\n"), state(""), state(None), state(HEAD), state(HEAD + "\n   \n\n## Results\n- on (\"yes\")\n"),
       state(HEAD + "<!-- off, or on (date \"words\") -->\n"), state(HEAD + "   <- to switch it on: `on (<date> \"<user's words>\")`\n")] == [None] * 7,
      [state(HEAD), state(HEAD + "<!-- off, or on (date \"words\") -->\n"), state(HEAD + "   <- to switch it on: `on (<date> \"<user's words>\")`\n")])
NO_LINE = ("the spec's \"## Independent review gate\" section has content but no line `Independent review gate: off` or `Independent review gate: on (… \"user words\")`,"
           " so it cannot be told whether the gate is on: %s")
got = [state(HEAD + rest + "\n") for rest in ("- nothing here", "- on (\"yes\")", "on (2026-01-01 \"review it\")", "The user said: switch it on", "<!-- hint -->\n- gate: on (\"yes\")")]
check("gate_state: a section with content but no recognisable gate line is \"unclear\", not None (rule changed for `- nothing here`) — a bare `- on (\"yes\")` was read as \"no gate line\" and the gate silently skipped",
      got == ["unclear"] * 5, got)
check("… gate_on_quote is None there, and gate_problem quotes the first content line",
      spec.gate_on_quote(HEAD + "- on (\"yes\")\n") is None and problem(HEAD + "<!-- hint -->\n\n- on (\"yes\")\n- more\n") == NO_LINE % "- on (\"yes\")", problem(HEAD + "<!-- hint -->\n\n- on (\"yes\")\n- more\n"))
MARKED = (("- **Independent review gate:** on (2026-01-01 \"review it\")", "on", "review it"), ("1. Independent review gate: on (\"yes\")", "on", "yes"),
          ("2) Independent review gate: on (\"yes\")", "on", "yes"), ("+ Independent review gate: off", "off", None), ("* **Independent review gate**: off", "off", None),
          ("- _Independent review gate_: on (\"yes\")", "on", "yes"), ("- `Independent review gate`: off   <- a note", "off", None), ("  12.  __Independent Review Gate:__ On (\"yes\")", "on", "yes"),
          ("- **Independent review gate:** maybe", "unclear", None))
got = [(state(HEAD + text + "\n"), spec.gate_on_quote(HEAD + text + "\n")) for text, _, _ in MARKED]
check("gate lines with the list markers - * + 1. 1) and markdown emphasis (* _ `) around the key are recognised — they used to be \"no gate line\", so a gate switched on was skipped",
      got == [(want, words) for _, want, words in MARKED], list(zip([text for text, _, _ in MARKED], got)))
check("a later marked line still overrides an earlier one (the last gate line decides)",
      state(HEAD + "- Independent review gate: on (\"yes\")\n+ **Independent review gate:** off\n") == "off" and state(HEAD + "- Independent review gate: off\n1. Independent review gate: on (\"yes\")\n") == "on")
PLACEHOLDERS = ("on (<date> \"<user's words>\")", "on (\"<user's words>\")", "on (2026-01-01 \" <words> \")", "On (<date> \"<user's words>\")   <- copied from the hint")
got = [(state(GATE + rest + "\n"), spec.gate_on_quote(GATE + rest + "\n")) for rest in PLACEHOLDERS]
check("quoted words that are a placeholder (they start with < and end with >) do not switch the gate on: \"unclear\" — the template hint copied literally used to read as on",
      got == [("unclear", None)] * len(PLACEHOLDERS), got)
check("words that only contain < or > are still the user's words (on)", [state(GATE + rest + "\n") for rest in ("on (\"<b>yes\")", "on (\"yes <now>\")", "on (\"a < b > c\")")] == ["on"] * 3)
check("gate_problem: one line that quotes the unclear line",
      problem(GATE + "on (user said yes)\n") == "the spec's last \"Independent review gate:\" line is neither `off` nor `on (… \"user words\")`, so it cannot be told whether the gate is on: - Independent review gate: on (user said yes)",
      problem(GATE + "on (user said yes)\n"))
check("gate_problem: None when the gate is on · off · absent", [problem(GATE + "on (\"yes\")\n"), problem(TEMPLATE_OFF), problem("")] == [None] * 3)

print("\nfailures: %d" % len(failures))
sys.exit(1 if failures else 0)
