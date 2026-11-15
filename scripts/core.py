# -*- coding: utf-8 -*-
"""**OQA Core — looks at the change and picks the verification checks (plan), then bundles the check results into one answer: is it done (verdict).**

    python core.py plan --repo <root> [--json]       writes .oqa/core.json and shows the picked checks as a table
    python core.py verdict --repo <root> [--json]    exit code 0 DONE · 1 INCOMPLETE · 3 NEEDS DECISION · 2 UNKNOWN
    python core.py rules                             the rule table

Why: what to verify, and whether the bundled results mean "done", must not be left to the memory of the conversation.

- **The rule table is the single `RULES` list in this file** — a check name is the same string as the "kind" of an evidence line
- What changed: `git diff HEAD` (name · status · line counts) + untracked new files. .oqa/ is left out (the same yardstick as evidence.py — editing
  the spec or recording evidence must not count as a change)
- The verdict looks only at the **evidence records** (`<repo>/.oqa/evidence.jsonl` — evidence.py): for each check, "is there evidence with the same
  fingerprint as the code now, and is its result good". Timestamps of runs, tables in the spec text and the verdict line of the report text are not
  read. From the spec text only the user's approval ("## Approval") and the quoted words that switch the gate on are read, and a hash of its
  expected results and finish line is compared with the one report.py stored in its evidence line (the spec changed after the report → not DONE)
- Check states: ok · failed · unknown · stale (another fingerprint — the code changed afterwards) · missing. Manual checks (reviews) are also
  INCOMPLETE without evidence — the main conversation records them with `evidence.py record --fingerprint <fingerprint at start>`
- `verdict(repo)` never raises — when it cannot judge it returns {"verdict": "UNKNOWN", "message": reason}
"""
import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import spec                                              # noqa: E402 — current_spec · current_problem · approved · read_text · gate_on_quote · gate_state · gate_problem
from evidence import has_numbered_items, fingerprint, section, read as read_evidence, check_evidence, damaged as damaged_evidence, spec_hash, is_text_file      # noqa: E402 — one definition of the fingerprint · the spec hash · "plain text file" shared with report.py
import impact                                            # noqa: E402 — head · read_map
import findings                                          # noqa: E402 — findings ledger (open high findings)
import traceability                                      # noqa: E402 — tests_for: which entries of the test list run a file
from init import is_test_name                            # noqa: E402 — the naming rule the test list is built from (`test_*.py` · `*_test.py`)

EXCLUDED = (".oqa/",)
NO_SPEC = "no current spec found"
NO_CHANGE = "there is no uncommitted change to verify — core.py plan and verdict must be run before committing (after a commit nothing is left to check, so this is not DONE)"
NO_CHECKS = "core.json lists changed files but no checks — nothing was verified; run core.py plan again"
# Whole words only (split on non-letters), so `checkout.py` or `account.py` do not switch the review on.
JUDGING_NAMES = re.compile(r"(?<![a-z])(?:verdict|judge|score|count|check|tally|fingerprint|valid)(?:s|d|ed|r|er|or|ing|ation|ate)?(?![a-z])", re.I)
# The conditions of the manual checks look at "this change" only — picking by words in the spec text, by the number of regexes in a whole file or by
# the number of table rows in the spec switched on Silent · Reasoning · Blind · Fuzz all four even for a small fix
BLIND_TRIGGER = re.compile(r"blind-check|blind review", re.I)   # Blind Review only when the spec names that judgement as a blind-review target
NOT_USED = re.compile(r"not used|skip", re.I)
INPUT_PARSING = re.compile(r"\bre\.\w+\(|(?<![A-Za-z])parse(?!r|_args|_known_args)|\.split\(")    # argparse · parser · subparsers · parse_args are command-line boilerplate — so that Fuzz is not switched on for every new tool
INPUT_PARSING_THRESHOLD = 3                              # this many re.… · parse · split on the **added lines** of one source .py file means input-parsing code was changed
SILENT_THRESHOLD = 30                                    # this many changed lines of source code call for the counting review (Silent)
BIG_CHANGE_LINES = 150
NEW_PY_THRESHOLD = 2

# File kinds — the first match from the top. What cannot be told is "unknown" (never folded into 0 or another kind)
KIND_TABLE = [
    ("test file", lambda path: bool(re.search(r"(^|/)tests?/", path) or re.search(r"(^|/)(test_[^/]+|[^/]+_test)\.py$", path))),
    ("source code", lambda path: path.endswith(".py")),
    ("ui", lambda path: Path(path).suffix.lower() in (".html", ".css", ".js")),
    ("doc", is_text_file),                               # the same "plain text file" the report passes over (evidence.is_text_file) — `.gitignore` and `requirements*.txt` are not on it, so they are "unknown"
    ("data/config", lambda path: Path(path).suffix.lower() in (".json", ".sql", ".yaml", ".yml", ".toml")),
]


def kind_of(path):
    for name, matches in KIND_TABLE:
        if matches(path):
            return name
    return "unknown"


def _code(facts):
    return [f for f in facts["files"] if f["kind"] == "source code"]


def _names(items, n=4):
    return ", ".join(items[:n]) + (" and %d more" % (len(items) - n) if len(items) > n else "")


def _always(facts):
    return "%d changed file(s)" % len(facts["files"])


def _py(facts):
    paths = [f["path"] for f in _code(facts)]
    return "%d changed source .py (%s)" % (len(paths), _names(paths)) if paths else None


def _trace(facts):
    # "when there are E numbers" — not only source .py but ui · config changes too (not when only docs · test files · unknown files changed)
    paths = [f["path"] for f in facts["files"] if kind_of(f["path"]) not in ("doc", "test file", "unknown")]
    return "%d changed code · ui · config (%s) · E numbers in the spec's \"Expected results\"" % (len(paths), _names(paths)) if paths and facts["has_items"] else None


def _ui(facts):
    paths = [f["path"] for f in facts["files"] if f["kind"] == "ui"]
    return "%d ui file(s) (%s)" % (len(paths), _names(paths)) if paths else None


def _silent(facts):
    words = sorted({w for f in _code(facts) for w in JUDGING_NAMES.findall(Path(f["path"]).name)})
    lines = sum(f["added"] + f["removed"] for f in _code(facts))
    parts = (["code file name contains " + "·".join(words)] if words else []) + (["%d changed lines of code" % lines] if lines >= SILENT_THRESHOLD else [])
    return " / ".join(parts) if parts else None


def _structure(facts):
    # test files are left out — adding two repro tests is not a structural change and must not switch on the reasoning review
    new = [f["path"] for f in facts["files"] if f["new"] and f["path"].endswith(".py") and kind_of(f["path"]) != "test file"]
    lines = sum(f["added"] + f["removed"] for f in facts["files"] if f["kind"] not in ("doc", "test file", "unknown"))      # long prose or long tests are not a structural change
    parts = (["%d new .py" % len(new)] if len(new) >= NEW_PY_THRESHOLD else []) + (["%d changed lines of code · ui · config" % lines] if lines >= BIG_CHANGE_LINES else [])
    return " / ".join(parts) if parts else None


def _risk(facts):
    # a line that says it is not used ("blind review: not used — reason") is left out (the Tools section is also where "fits but not used, and why" is written)
    words = sorted({w for line in facts["tools"].splitlines() if not NOT_USED.search(line) for w in BLIND_TRIGGER.findall(line)})
    return "the spec's \"Tools\" section names a blind review (" + "·".join(words) + ")" if words else None


def _input(facts):
    hits = ["%s(%d)" % (path, count) for path, count in facts["input_parsing"] if count >= INPUT_PARSING_THRESHOLD]
    return "re.… · parse · split on added lines: " + _names(hits) if hits else None


def _manual_mutation(facts):
    # the mutation tool (ast) cannot change the inside of a regex or a message text — when such lines were added or the change is big, revert by hand
    # and see whether the repro test bites (manual_mutate.py records the evidence)
    hits = ["%s(%d)" % (path, count) for path, count in facts["input_parsing"] if count >= 1]
    lines = sum(f["added"] + f["removed"] for f in _code(facts))
    parts = (["regex · string splitting on added lines: " + _names(hits)] if hits else []) + (["%d changed lines of code" % lines] if lines >= SILENT_THRESHOLD else [])
    return " / ".join(parts) if parts else None


def _gate(facts):
    return "the spec's \"## Independent review gate\" is on" if facts["gate_on"] else None


def _baseline(facts):
    return "no impact map (.oqa/impact-map.json) — take a baseline with baseline.py" if not facts["has_map"] else None


# ── Rule table — check name · condition · function that gives the reason · can it have been done by hand when there is no record ──
# The name is the same string as the "kind" of the evidence lines
RULES = [
    ("Regression Test", "always when anything changed", _always, False),
    ("Mutation Test", "source .py (test files excluded) changed", _py, False),
    ("Manual Mutation Test", "kinds of change the mutation tool cannot make —regex · string splitting (re.… · parse · split) on added lines of source .py, or %d or more changed lines of code. manual_mutate.py records the evidence" % SILENT_THRESHOLD, _manual_mutation, False),
    ("Requirements Traceability", "code · ui · config changed and the spec's \"Expected results\" has numbered E items (not when only docs · test files changed)", _trace, False),
    ("E2E UI Test", "ui files (.html · .css · .js) changed", _ui, False),
    ("Silent Failure Review", "verdicts · counting · data handling — a changed code file name contains verdict · judge · score · count · check · tally · fingerprint · valid, or %d or more changed lines of code" % SILENT_THRESHOLD, _silent, True),
    ("Reasoning Review", "structural change — %d or more new .py (test files excluded) · %d or more changed lines of code · ui · config" % (NEW_PY_THRESHOLD, BIG_CHANGE_LINES), _structure, True),
    ("Blind Review", "high-risk judgement (money · permissions · interpretation of a rule · whether a requirement is met) — when the spec's \"## Tools\" section names blind-check · \"blind review\" (the same words in results or explanatory text do not switch it on)", _risk, True),
    ("Fuzz Test (LLM)", "input parsing — %d or more re.… · parse · split on the added lines of one source .py file" % INPUT_PARSING_THRESHOLD, _input, True),
    ("Independent Review Gate", "the spec's independent review gate is on", _gate, False),
    ("Baseline", "no impact map (repository seen for the first time)", _baseline, True),
]


# ───────── Reading what changed ─────────

def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), "-c", "core.quotepath=false"] + list(args),
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)


# Every diff whose output is read here is asked for without colour, without an external diff program and without text conversion — with
# `color.ui always` or `diff.external` in the user's git settings the lines no longer start with `+`, and that read as "nothing was added".
# The prefixes are fixed too: with `diff.noPrefix` or `diff.mnemonicPrefix` the file line is `+++ path` or `+++ w/path`, not `+++ b/path`
DIFF = ("diff", "HEAD", "--no-color", "--no-ext-diff", "--no-textconv", "--src-prefix=a/", "--dst-prefix=b/")


def _failed(out, command, values=None):
    """Called when a git command whose output is the list of changes exited with an error: the answer is None (unknown), never the empty output read as
    "no changes". The command and git's first error line go into values["error"] for the message"""
    if values is not None:
        values["error"] = "git %s failed (exit code %s): %s" % (command, out.returncode, ((out.stderr or "").strip().splitlines() or ["no error text"])[0][:200])
    return None


def _excluded(path):
    return any(path == prefix.rstrip("/") or path.startswith(prefix) for prefix in EXCLUDED)


def changed(repo, values=None):
    """[{path, status (A·M·D·new), new, added, removed}] — None when not a git repository, and None when a git command that lists the changes
    exits with an error (values["error"] then names the command) — a failed command is unknown, not an empty list"""
    # one git call takes a noticeable fraction of a second — keep the number of calls low (one HEAD check · one diff · one ls-files)
    head = impact.head(repo)
    if values is not None:
        values["HEAD"] = head
    has_head = head is not None
    if not has_head and _git(repo, "rev-parse", "--git-dir").returncode != 0:
        return None
    files = {}
    if has_head:
        diff = _git(repo, *DIFF, "--no-renames", "--numstat", "--summary")
        if diff.returncode != 0:
            return _failed(diff, "diff HEAD --numstat", values)
        for line in diff.stdout.splitlines():
            cols = line.split("\t")
            if len(cols) == 3:
                files[cols[2]] = {"path": cols[2], "status": "M", "new": False,
                                  "added": int(cols[0]) if cols[0].isdigit() else 0, "removed": int(cols[1]) if cols[1].isdigit() else 0}
                continue
            m = re.match(r"^\s*(create|delete) mode \d+ (.+)$", line)
            if m and m.group(2) in files:
                files[m.group(2)].update(status="A" if m.group(1) == "create" else "D", new=m.group(1) == "create")
    listed = _git(repo, "ls-files", "--others", "--exclude-standard")
    if listed.returncode != 0:
        return _failed(listed, "ls-files --others", values)
    untracked = listed.stdout.splitlines()
    if not has_head:                                     # without a single commit, what is in the index is a new file too
        listed = _git(repo, "ls-files", "--cached")
        if listed.returncode != 0:
            return _failed(listed, "ls-files --cached", values)
        untracked += listed.stdout.splitlines()
    for path in untracked:
        if path and path not in files:
            try:
                count = len((Path(repo) / path).read_bytes().splitlines())
            except OSError:
                count = 0
            files[path] = {"path": path, "status": "new", "new": True, "added": count, "removed": 0}
    return [f for path, f in sorted(files.items()) if not _excluded(path)]


def change_fingerprint(repo, files):
    """Names of the changed files + hash of their content now — did the change grow or alter after the plan"""
    h = hashlib.sha1()
    for f in files:
        try:
            inner = hashlib.sha1((Path(repo) / f["path"]).read_bytes()).hexdigest()
        except OSError:
            inner = "missing"
        h.update(("%s\0%s\n" % (f["path"], inner)).encode("utf-8"))
    return {"files": [f["path"] for f in files], "hash": h.hexdigest()[:16]}


def _relative(repo, path):
    try:
        return Path(path).resolve().relative_to(Path(repo).resolve()).as_posix()
    except ValueError:
        return str(path)


def added_text(repo, f):
    """Only the lines added to that file this time, joined — the whole file when it is new. Empty text when a new file cannot be read ·
    None when git diff exits with an error (unknown — not "nothing added")"""
    if f.get("new"):
        try:
            return (repo / f["path"]).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
    out = _git(repo, *DIFF, "-U0", "--", f["path"])
    if out.returncode != 0:
        return None
    return "\n".join(line[1:] for line in out.stdout.splitlines() if line.startswith("+") and not line.startswith("+++"))


def added_texts(repo, files, values=None):
    """{path: only the lines added to that file this time, joined} — one git diff for all files instead of calling added_text per file (git calls are slow).
    A new file is the whole file (the file is read — git is not started). The values equal those of added_text.
    None when git diff exits with an error (values["error"] names the command), and None when a file line of the diff is not `+++ b/<path>` or
    `+++ /dev/null` (the line is in values["error"]) — the checks picked from added lines would otherwise silently drop out"""
    out = {}
    old = [f["path"] for f in files if not f.get("new")]
    for f in files:
        if f.get("new"):
            out[f["path"]] = added_text(repo, f)
    if old:
        current, in_header = None, False                  # in_header: between a `diff --git` line and the first `@@` — only there is `+++ …` a file line (an added line `++ x` reads `+++ x` too)
        lines = {path: [] for path in old}
        diff = _git(repo, *DIFF, "-U0", "--no-renames", "--", *old)
        if diff.returncode != 0:
            return _failed(diff, "diff HEAD -U0", values)
        for line in diff.stdout.splitlines():
            if line.startswith("diff --git "):
                current = None                             # until the next `+++ b/path` it is unknown which file the lines belong to
                in_header = True
            elif in_header and line.startswith("@@"):
                in_header = False
            elif in_header and line.startswith("+++ "):
                path = line[4:].strip().strip('"')
                if path.startswith("b/"):
                    current = path[2:]
                elif path != "/dev/null":                  # a deleted file has no new side and adds no line; anything else is a file this function cannot name
                    if values is not None:
                        values["error"] = "git diff HEAD -U0 gave a file line that cannot be read (expected `+++ b/<path>`): %s" % line[:200]
                    return None
            elif not in_header and line.startswith("+"):
                if current not in lines:                   # an added line of a file that was not recognised (a path git wrote in another form) — not "nothing added"
                    if values is not None:
                        values["error"] = "git diff HEAD -U0 gave added lines for a file that cannot be matched to the changed files: %s" % (current or "no file line")
                    return None
                lines[current].append(line[1:])
        for path in old:
            out[path] = "\n".join(lines[path])
    return out


def unlisted_tests(repo, files):
    """Paths of the changed or new files that carry a test name (`test_*.py` · `*_test.py`, the rule init.py lists by) and that no entry of
    `.oqa/tests.json` runs — by its command, or by the lines the impact map recorded for it (a command that runs a whole folder).
    Such a test is never run by report.py, so the plan says it before the report does. Deleted files are left out. The list is read as it is
    (nothing is started here); a missing or unreadable list has no entry, so every such file is named"""
    try:
        entries = json.loads((Path(repo) / ".oqa" / "tests.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        entries = []
    tests = [t for t in entries if isinstance(t, dict) and t.get("name") and isinstance(t.get("command"), str)] if isinstance(entries, list) else []
    recorded = (impact.read_map(repo) or {}).get("tests") or {}
    return [f["path"] for f in files if f["status"] != "D" and is_test_name(f["path"]) and not traceability.tests_for(tests, f["path"], recorded, repo)]


def read_finish_line(spec_text):
    """(set of finish-line check names | None (no section), list of unknown names) — reads rule-table check names from the list lines (`- a · b`) of the spec's "## Finish line" section.
    The verification this work needs is decided by **the spec the user approved**, not by Core's threshold guesses (otherwise Core keeps picking another review for its own fix and never ends).
    Names that are not in the rule table are returned, not dropped — the verdict raises them as "to confirm" (so that a typo does not silently drop a needed check)"""
    body = section(spec_text or "", "Finish line")
    if body is None:
        return None, []
    names = {rule[0] for rule in RULES}
    picked, unknown = set(), []
    for line in body.splitlines():
        m = re.match(r"^\s*[-*]\s+(.*)$", line)
        for word in (re.split(r"\s+·\s+|,\s*", m.group(1)) if m else []):
            word = word.strip().strip("`")
            if word in names:
                picked.add(word)
            elif word:
                unknown.append(word)
    return picked, unknown


def plan(repo):
    """Writes core.json and returns its content. {"unknown": reason} when not a git repository or when a git command that reads the change failed
    (the reason names the command) — nothing is written then.
    "spec_problem": the reason of spec.current_problem when no current spec can be told although spec files exist (else None) — printed by print_plan"""
    repo = Path(repo).resolve()
    values = {}
    files = changed(repo, values)
    if files is None:
        return {"unknown": values.get("error") or "not a git repository: %s" % repo}
    for f in files:
        f["kind"] = kind_of(f["path"])
    spec_path = spec.current_spec(repo) if (repo / ".oqa" / "specs").is_dir() else None
    spec_text = (spec.read_text(spec_path) or "") if spec_path else ""
    # why there is no spec although spec files are there (`.oqa/current` names none, or it is missing and several exist) — said at plan time already, not only by the verdict
    spec_problem = spec.current_problem(repo) if spec_path is None else None
    parsing = []
    code_files = _code({"files": files})
    added = added_texts(repo, code_files, values)
    if added is None:
        return {"unknown": values.get("error") or "git diff failed"}
    for f in code_files:
        parsing.append((f["path"], len(INPUT_PARSING.findall(added.get(f["path"], "")))))
    # Blind looks at the "Tools" section only — looking at the whole spec switched it on for "blind review" text written under the results too
    facts = {"files": files, "tools": section(spec_text, "Tools") or "", "has_items": has_numbered_items(spec_text), "gate_on": bool(spec.gate_on_quote(spec_text)),
             "input_parsing": parsing, "has_map": impact.read_map(repo) is not None}
    checks = []
    finish, finish_unknown = read_finish_line(spec_text)
    if files:
        for name, condition, func, _manual in RULES:
            basis = func(facts)
            # Finish line — when the spec has "## Finish line", the checks named there are "required" and what the rule table picks beyond that is
            # "recommended" (the verdict does not demand it). Without a finish line everything the rule table picks is required. The independent review
            # gate of a spec that switched the gate on is required even when the finish line does not name it (the user switched it on)
            if basis:
                # a finish-line section without a readable check (prose only · malformed) is not "0 checks required" but unread — everything picked is required, and the verdict asks to confirm
                required = not finish or name in finish or (name == "Independent Review Gate" and facts["gate_on"])
                checks.append({"name": name, "condition": condition, "reason": basis, "required": required})
            elif finish and name in finish:
                checks.append({"name": name, "condition": condition, "reason": "named in the spec's \"## Finish line\" (the rule table did not pick it)", "required": True})
    result = {"created": time.strftime("%Y-%m-%dT%H:%M:%S"), "HEAD": values.get("HEAD"), "change_fingerprint": change_fingerprint(repo, files),
              "spec_file": _relative(repo, spec_path) if spec_path else None, "spec_problem": spec_problem, "checks": checks, "finish_line": sorted(finish) if finish is not None else None, "finish_line_unknown": finish_unknown,
              "unlisted_tests": unlisted_tests(repo, files),
              # what the rule table took from the spec text besides the finish line and the gate — the verdict compares it with the spec now
              "spec_picks": {"blind_review": bool(_risk(facts)), "numbered_items": bool(facts["has_items"])},
              "files": [{"path": f["path"], "kind": f["kind"], "status": f["status"], "added": f["added"], "removed": f["removed"]} for f in files]}
    if not files:
        result["reason"] = "no changes" + ("" if facts["has_map"] else " — and no impact map (for a repository seen for the first time, take a baseline with baseline.py)")
    (repo / ".oqa").mkdir(exist_ok=True)
    (repo / ".oqa" / "core.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return result


# ───────── Verdict ─────────
# The verdict looks only at the **evidence records** (<repo>/.oqa/evidence.jsonl — evidence.py): for each check, "is there evidence with the same
# fingerprint as the code now, and is its result good".
# Comparing run timestamps with the creation time of core.json (pairing start · end lines), guessing the repository from command text and reading gate
# tables or the report's verdict line out of the spec text by pattern are all guesses — and every such guess opens a path to a wrong DONE.
# One fingerprint closes what those guesses were meant to close:
#   · tests run on old code · a review run before the fix · code changed after approval · code changed after the report → another fingerprint, so "stale"
#   · runs in another repository → the evidence file is per repository, so they are not seen
#   · a verdict line copied into the spec text · an approval table written by hand → not read (only the user's approval "## Approval" and the quoted
#     words that switch the gate on are read from the spec text)

def _verdict(repo):
    repo = Path(repo).resolve()
    path = repo / ".oqa" / "core.json"
    if not path.is_file():
        return {"verdict": "UNKNOWN", "exit_code": 2, "message": "no core.json — run core.py plan first"}
    core = json.loads(path.read_text(encoding="utf-8"))
    created = str(core.get("created") or "")
    if not created:
        return {"verdict": "UNKNOWN", "exit_code": 2, "message": "core.json has no created time"}
    now = fingerprint(repo)
    if now is None:
        return {"verdict": "UNKNOWN", "exit_code": 2, "message": "cannot measure the code fingerprint now — evidence cannot be matched (not folded into DONE)"}
    lines = read_evidence(repo)
    reasons, to_confirm, to_ask = [], [], []
    checks = []
    for c in core.get("checks") or []:
        state, basis, _ = check_evidence(lines, c["name"], now)
        checks.append({"name": c["name"], "state": state, "basis": basis, "required": c.get("required", True)})      # an old core.json without "required" counts every check as required
    # An empty check list is never DONE. The plan picks no check only when nothing is uncommitted — and "nothing to check" must not read as "everything
    # checked": committing first and asking afterwards gave DONE with no evidence at all
    if not checks:
        to_confirm.append(NO_CHECKS if core.get("files") else NO_CHANGE)
    # Lines of the evidence file that cannot be read are skipped by the reader — a garbled later `fail` would leave an earlier `pass` standing
    broken = damaged_evidence(repo)
    if broken:
        to_confirm.append("%d line(s) of .oqa/evidence.jsonl cannot be read as evidence records — a damaged line may hide a failure; repair the file or record the evidence again" % broken)
    if core.get("finish_line_unknown"):
        to_confirm.append("the spec's \"## Finish line\" names checks that are not in the rule table: %s" % ", ".join(core["finish_line_unknown"]))
    if core.get("finish_line") == [] and not core.get("finish_line_unknown"):
        to_confirm.append("the spec's \"## Finish line\" section has no list of checks (form `- check · check`) — the finish line could not be read, so every picked check was taken as required")
    # The finish line was read at plan time and written into core.json — when the spec's finish line changed afterwards, do not judge by the old
    # "required" flags (the spec is outside the change fingerprint)
    spec_text_now = (spec.read_text(repo / core["spec_file"]) or "") if core.get("spec_file") else ""
    finish_now, unknown_now = read_finish_line(spec_text_now)
    if (sorted(finish_now) if finish_now is not None else None) != core.get("finish_line") or unknown_now != (core.get("finish_line_unknown") or []):
        reasons.append("the spec's \"## Finish line\" changed after core.py plan — run core.py plan again")
    # The rule table also reads the spec's "## Tools" section (a blind review named there) and whether the expected results are numbered. A blind
    # review written into the spec after the plan was in no check list, so the verdict did not ask for it — while a second plan on the same code
    # made it required. Only "the spec asks for more now" counts; a core.json without the key (an older plan) is accepted as before
    picks = core.get("spec_picks")
    if isinstance(picks, dict) and core.get("files"):
        more = (["a blind review in \"## Tools\""] if _risk({"tools": section(spec_text_now, "Tools") or ""}) and not picks.get("blind_review") else []) + (
            ["numbered expected results"] if has_numbered_items(spec_text_now) and not picks.get("numbered_items") else [])
        if more:
            reasons.append("the spec asks for more than it did at core.py plan (%s) — run core.py plan again" % " · ".join(more))
    # Findings ledger — an open high finding means not DONE. When it cannot be read it is not folded into "none"
    open_now = None
    try:
        open_now = {s: [f["id"] for f in findings.open_findings(repo, s)] for s in findings.SEVERITIES}
        if open_now["high"]:
            reasons.append("open high findings %d: %s — findings.py close (accepted needs a repro test · rejected and deferred need a reason)" % (len(open_now["high"]), ", ".join(open_now["high"])))
        # Findings tied to a check — that review is not finished just by being run (they must be closed whatever the severity). A check name that is
        # not in the rule table is "to confirm" (so that a typo does not untie the finding)
        check_names, tied = {rule[0] for rule in RULES}, {}
        for f in findings.open_findings(repo):
            if f.get("check"):
                tied.setdefault(f["check"], []).append(f["id"])
        for name, ids in sorted(tied.items()):
            if name in check_names:
                reasons.append("open findings of %s %d: %s — the findings of that review must be closed before it is done" % (name, len(ids), ", ".join(ids)))
            else:
                to_confirm.append("the check name of finding %s is not in the rule table: %s" % (", ".join(ids), name))
    except ValueError as error:
        to_confirm.append("cannot read the findings ledger — %s" % error)
    # Are the picked checks for the change now
    git_values = {}
    files_now = changed(repo, git_values)
    if files_now is None:
        to_confirm.append("git could not be read, so the change now is unknown" + (" (%s)" % git_values["error"] if git_values.get("error") else ""))
    elif change_fingerprint(repo, files_now) != core.get("change_fingerprint"):
        reasons.append("the change differs from the one planned — run core.py plan again (the picked checks are not for the change now)")
    # Spec · user approval — the one thing read from the spec text, because only a person can give it (no tool can record an approval as evidence)
    spec_path = spec.current_spec(repo) if (repo / ".oqa" / "specs").is_dir() else None
    spec_text = (spec.read_text(spec_path) or "") if spec_path else ""
    spec_now = _relative(repo, spec_path) if spec_path else None
    # Why the current spec cannot be told (`.oqa/current` names no spec, or it is missing and several specs exist). It becomes the FIRST reason of the
    # verdict (put in front further down), and "differs from core.json" is not said on top of it: that headline sent the reader to plan again, and a
    # plan made while the pointer is wrong runs without any spec
    pointer_problem = spec.current_problem(repo) if spec_path is None else None
    if pointer_problem:
        pass
    elif core.get("spec_problem"):
        # the plan was made while the spec could not be told: no finish line, no expected results and no gate were read — whatever the pointer says now
        reasons.append("core.py plan ran without a usable spec (%s) — run core.py plan again" % core["spec_problem"])
    elif spec_now != core.get("spec_file"):
        # compared always — also "no spec at plan time, one now" and the reverse (it used to be compared only when core.json named a spec)
        reasons.append("the current spec differs from the spec in core.json (%s) — run core.py plan again" % (core.get("spec_file") or "none"))
    if spec_path is None:
        if not pointer_problem:                          # "no spec" and its hint are only for the case that there really is none
            to_confirm.append(NO_SPEC)
    else:
        not_approved = spec.approval_problem(spec_path)
        if not_approved:
            reasons.append(not_approved)
    # Verification report — the "Verification Report" evidence recorded by report.py (the report text written into the spec is a copy for people and is not read)
    report = "not required"
    if any(c["name"] in ("Mutation Test", "Regression Test", "Requirements Traceability") and c["required"] for c in checks):
        state, basis, current = check_evidence(lines, "Verification Report", now)
        report = {"ok": "all passed", "failed": "incomplete", "unknown": "cannot judge", "stale": "code changed after report", "missing": "no report"}[state]
        if state == "failed":
            reasons.append("verification report: %s" % basis)
        elif state == "unknown":
            to_confirm.append("verification report: %s" % basis)
        elif state == "stale":
            reasons.append("code changed after report — run report.py again (%s)" % basis)
        elif state == "missing":
            reasons.append("no verification report — run report.py")
        elif current[-1].get("equivalent"):
            # when mutants were left out as equivalent, a person looks at the reason even if "all passed" — so that one line in the spec is not a self-granted exemption
            to_confirm.append("the verification report left %s equivalent mutant(s) out of incomplete — confirm those lines of the report and their reasons" % current[-1]["equivalent"])
        # Which spec the report was made for — report.py verifies against the spec given with --spec and writes it into the evidence line (key "spec").
        # The fingerprint leaves .oqa/ out, so a report made for another spec has the fingerprint now all the same; it is not evidence under the
        # current spec. A line without the key (an older line) is accepted as before
        # Which commit the report measured the change against (key "head"). The report is about a diff — HEAD to the files — and the fingerprint
        # holds only the files: after `git reset --soft HEAD~1` the uncommitted change is bigger, the files and the fingerprint are the same, and
        # the report of the small change was taken for the big one. A line without the key (an older line) is accepted as before
        made_at = current[-1].get("head") if current else None
        if made_at and made_at != impact.head(repo):
            report = "made against another commit"
            reasons.append("the verification report was made against another commit (HEAD %s then) — the change it measured is not the change now; run report.py again" % str(made_at)[:7])
        made_for = current[-1].get("spec") if current else None
        if made_for and spec_path is not None and made_for != spec_now:
            report = "made for another spec"
            reasons.append("the verification report was made for another spec (%s) — run report.py with the current spec" % made_for)
        # What that spec promised when the report was made — report.py writes a short hash of the spec's "## Expected results" and "## Finish line"
        # into the same line (key "spec_hash"). The spec lies under .oqa/, outside the fingerprint: an `- E2 …` added after a passing report left
        # every piece of evidence valid. A line without the key is accepted as before
        elif current and current[-1].get("spec_hash") and spec_path is not None and current[-1]["spec_hash"] != spec_hash(spec_text):
            report = "spec changed after report"
            reasons.append("the spec's expected results or finish line changed after the report — run report.py again")
    # Independent review gate — approved · rejected recorded together with "the fingerprint when the review bundle was made". Only whether the gate is on comes from the spec text (the user's words)
    gate = None
    gate_state = spec.gate_state(spec_text) if spec_text else None
    if gate_state == "unclear":                          # neither a clean off nor an on with the user's quoted words — not taken as off (that would skip the gate), asked instead
        to_confirm.append(spec.gate_problem(spec_text))
    if gate_state == "on":
        rounds = sum(1 for d in lines if d.get("kind") == "Independent Review Gate")
        gate_state_now, _, gate_lines = check_evidence(lines, "Independent Review Gate", now)
        gate_approved = gate_state_now == "ok"
        # What the spec promised when the gate answered (key "spec_hash", written by `evidence.py record`). The reviewer judged the code against
        # those expected results; the spec is outside the fingerprint, so a reworded result kept the approval. A line without the key is accepted as before
        if gate_approved and gate_lines and gate_lines[-1].get("spec_hash") and gate_lines[-1]["spec_hash"] != spec_hash(spec_text):
            gate_approved = False
            reasons.append("the spec's expected results or finish line changed after the independent review gate approved — run the gate again")
        gate = {"rounds": rounds, "approved": gate_approved}
        if not gate_approved and rounds >= 3:
            to_ask.append("Independent Review Gate: %d rounds without approval for the code now — ask the user whether to run it once more" % rounds)
        if not any(c["name"] == "Independent Review Gate" for c in checks):
            reasons.append("the spec's independent review gate is on but core.json has no such check — run core.py plan again")
    # Only "required" checks count for the verdict — "recommended" ones (outside the finish line) only show their state. But a recommended check with
    # **failed** evidence is INCOMPLETE too (something that was run and went wrong is not overlooked)
    missing = [c["name"] for c in checks if c["state"] == "missing" and c["required"]]
    stale = [c["name"] for c in checks if c["state"] == "stale" and c["required"]]
    failed = [c["name"] for c in checks if c["state"] == "failed"]
    to_confirm += ["%s — result unknown (%s)" % (c["name"], c["basis"]) for c in checks if c["state"] == "unknown" and c["required"]]
    reasons += ["missing check: %s" % c for c in missing] + ["check with evidence for old code only: %s" % c for c in stale] + ["failed check: %s" % c for c in failed]
    if pointer_problem:
        reasons.insert(0, pointer_problem)               # the first thing to settle: until the spec is known, the other reasons cannot be acted on
    if to_ask:
        word, code = "NEEDS DECISION", 3
    elif reasons:
        word, code = "INCOMPLETE", 1
    elif to_confirm:
        word, code = "NEEDS DECISION", 3
    else:
        word, code = "DONE", 0
    message = {"DONE": "all %d required check(s) have evidence for the code now (fingerprint %s)" % (sum(1 for c in checks if c["required"]), now),      # never with an empty check list — see NO_CHANGE
               "INCOMPLETE": "; ".join(reasons), "NEEDS DECISION": "; ".join(to_ask + to_confirm + reasons)}[word]
    return {"verdict": word, "exit_code": code, "message": message, "checks": checks, "missing_checks": missing, "stale_checks": stale, "failed_checks": failed, "to_confirm": to_confirm, "to_ask": to_ask,
            "reasons": reasons, "report": report, "review_gate": gate, "spec_file": core.get("spec_file"), "core_created": created, "fingerprint": now, "open_findings": open_now}


def verdict(repo):
    """Safe to call from automation — never raises.When it cannot judge: {"verdict": "UNKNOWN", "exit_code": 2, "message": reason}"""
    try:
        return _verdict(repo)
    except Exception as error:                           # noqa: BLE001 — callers must never see an exception; the reason is kept
        return {"verdict": "UNKNOWN", "exit_code": 2, "message": "error during verdict: %s: %s" % (type(error).__name__, error)}


# ───────── Printing ─────────

def rules_table_text():
    return "\n".join(["| check | condition |", "|---|---|"] + ["| %s | %s |" % (rule[0], rule[1]) for rule in RULES])


def print_plan(result):
    if "unknown" in result:
        print("unknown — " + result["unknown"])
        return
    print("Core plan · %s · HEAD %s · spec %s" % (result["created"], (result["HEAD"] or "none")[:10], result["spec_file"] or "none"))
    if result.get("spec_problem"):
        print("spec problem: %s" % result["spec_problem"])
    print("\n%d changed file(s)" % len(result["files"]))
    for f in result["files"]:
        print("  %-11s %-4s +%-4d -%-4d %s" % (f["kind"], f["status"], f["added"], f["removed"], f["path"]))
    if result.get("unlisted_tests"):
        # one line, before the checks: report.py runs only what the test list names, so such a file would come back as "cannot judge"
        print("test file(s) not listed in .oqa/tests.json: %s — add an entry for each (or run `init.py --force`); report.py does not run a test that is not listed" % ", ".join(result["unlisted_tests"]))
    if not result["checks"]:
        print("\nno checks — " + result.get("reason", ""))
        return
    print("\nfinish line: %s" % ("the spec's \"## Finish line\" — " + " · ".join(result["finish_line"]) if result.get("finish_line") is not None else "the spec has no \"## Finish line\" — every check the rule table picked is required"))
    if result.get("finish_line_unknown"):
        print("finish-line names not in the rule table: %s" % ", ".join(result["finish_line_unknown"]))
    print("\n| check | required | reason |\n|---|---|---|")
    for row in result["checks"]:
        print("| %s | %s | %s |" % (row["name"], "required" if row.get("required", True) else "recommended", row["reason"]))


def print_verdict(result):
    print("Core verdict: %s (exit code %d) — %s" % (result["verdict"], result["exit_code"], result["message"]))
    # An INCOMPLETE message lists only the reasons; what also has to be confirmed is shown here so it is not lost behind them
    if result["verdict"] == "INCOMPLETE" and result.get("to_confirm"):
        print("also to confirm: %s" % "; ".join(result["to_confirm"]))
    if NO_SPEC in (result.get("to_confirm") or []):      # only the real "no spec" case — a `.oqa/current` that names no spec has its own line and no such hint
        print("hint: there is no spec yet — write .oqa/specs/<name>.md (expected results, finish line), get the user's approval, then run report.py with --spec")
    if result.get("checks"):
        print("\n| check | required | state | evidence |\n|---|---|---|---|")
        for row in result["checks"]:
            print("| %s | %s | %s | %s |" % (row["name"], "required" if row.get("required", True) else "recommended", row["state"], row["basis"]))
    if result.get("open_findings"):
        print("\nopen findings: high %d · medium %d · low %d" % tuple(len(result["open_findings"][s]) for s in ("high", "medium", "low")))
    # with no check at all (nothing left to verify) "not required" would read as if the work needed no report — the line is left out then
    if "report" in result and (result.get("checks") or result["report"] != "not required"):
        print("\nverification report: %s" % result["report"])
    if result.get("review_gate"):
        print("Independent Review Gate: %d round(s) · approval for the code now %s" % (result["review_gate"]["rounds"], "yes" if result["review_gate"]["approved"] else "no"))
    if result.get("fingerprint"):
        print("fingerprint now: %s" % result["fingerprint"])


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    parser = argparse.ArgumentParser(description="OQA Core — plan the verification checks · bundled verdict")
    parser.add_argument("action", choices=["plan", "verdict", "rules"])
    parser.add_argument("--repo", default=".")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.action == "rules":
        print(rules_table_text())
        return 0
    if args.action == "plan":
        result = plan(args.repo)
        print(json.dumps(result, ensure_ascii=False, indent=1)) if args.json else print_plan(result)
        return 2 if "unknown" in result else 0
    result = verdict(args.repo)
    print(json.dumps(result, ensure_ascii=False, indent=1)) if args.json else print_verdict(result)
    return result["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
