# -*- coding: utf-8 -*-
"""**Fingerprint of the code as it is on disk, and the evidence records tied to it — did the code change after a check was run.** report.py (writing) and core.py (staleness) use the same functions.

A temporary index is started from the tree of HEAD (when there is a commit), the working tree is added to it exactly as git would store it
(.gitignore respected for files git does not track; a tracked file is in, also when .gitignore names it) and the tree hash is taken. .oqa/ is left out —
if writing the report into the spec changed the fingerprint, the report would always be stale. A commit with the same content has the same fingerprint.
When it cannot be measured the answer is None (unknown) — never reported as "stale".
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

EXCLUDED = (".oqa",)
REPORT_HEADING = "### Verification Report"
ITEM_PATTERN = re.compile(r"^\s*[-*]\s*(E\d+)\b\s*(.*)$")       # numbered items of the spec's "## Expected results" — one definition shared with traceability.py


def section(text, heading):
    """Body of the `## heading` section (up to the next `## `) — None when the section is missing. Shared by spec, trace, report and core.
    The heading is matched from its start and whatever its letter case, for every caller: a heading typed with other capitals (`## Expected Results`,
    `## approval`, `## tools`, `## finish line`) must not read as "no such section" — its checks would silently drop out of the plan"""
    m = re.search(r"^## %s[^\n]*\n(.*?)(?=^## |\Z)" % re.escape(heading), text or "", re.M | re.S | re.I)
    return m.group(1) if m else None


def has_numbered_items(spec_text):
    body = section(spec_text, "Expected results")
    return body is not None and any(ITEM_PATTERN.match(line) for line in body.splitlines())


def spec_hash(spec_text):
    """A short hash of what the spec promises: the bodies of its "## Expected results" and "## Finish line" sections (line ends and outer blank lines
    aside; a missing section is not the same as an empty one). report.py stores it in the Verification Report evidence line and core.py compares it
    with the spec now — the fingerprint leaves .oqa/ out, so an expected result added after a passing report would otherwise go unnoticed"""
    parts = []
    for heading in ("Expected results", "Finish line"):
        body = section((spec_text or "").replace("\r\n", "\n").replace("\r", "\n"), heading)
        parts.append("missing" if body is None else "present:" + body.strip())
    return hashlib.sha1("\0".join(parts).encode("utf-8")).hexdigest()[:12]


# A changed non-Python file is "plain text" only when it is known to be text (or a picture): these suffixes and these file names — one definition for
# the plan (core.py, kind "doc") and the report (report.py, "not code the machine failed to see"). Every other changed non-Python file (.js · .go · .rs ·
# .ini · Dockerfile · Makefile · a file without suffix …) is code no tool looked at → unknown.
# The list says what is safe to pass over, not what is code — a kind of file nobody thought of lands on the unknown side.
# `.gitignore` is not on it: a changed .gitignore can hide a new code file from every tool (the list of changes, the fingerprint, the copy), so it
# must be looked at. `requirements*.txt` and `constraints*.txt` end in .txt but decide which code runs — not plain text either
TEXT_SUFFIXES = {".md", ".txt", ".rst", ".adoc", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".pdf"}
TEXT_NAMES = {"LICENSE", "NOTICE", "CHANGELOG", "AUTHORS", ".gitattributes", ".editorconfig"}
DEPENDENCY_LIST = re.compile(r"^(?:requirements|constraints)[^/]*\.txt$", re.I)


def is_text_file(file):
    """Is this changed non-Python file one the tools may pass over as plain text — only a known text suffix (any letter case) or a known file name,
    and not a dependency list (`requirements*.txt` · `constraints*.txt`)"""
    path = Path(str(file).replace("\\", "/"))
    if DEPENDENCY_LIST.match(path.name):
        return False
    return path.suffix.lower() in TEXT_SUFFIXES or path.name in TEXT_NAMES


def report_section(spec_text):
    """Body of the verification report — from a line-start `### Verification Report` inside the "## Results" section up to the next `##` / `###` (where report.py writes it). A text without "## Results" is searched as a whole. None when missing.
    Every reader of the fingerprint, the verdict line and the unknown line uses this one section — otherwise the verdict of a fake section copied in front of "## Results" could be paired with the fingerprint of the real report"""
    body = section(spec_text, "Results")
    m = re.search(r"^%s\n(.*?)(?=^#{2,3} |\Z)" % re.escape(REPORT_HEADING), body if body is not None else (spec_text or ""), re.M | re.S)
    return m.group(1) if m else None


def recorded_fingerprint(spec_text):
    """Fingerprint of the report written in the spec — None when there is no report, "unknown" when the report exists but the fingerprint could not be measured"""
    body = report_section(spec_text)
    m = re.search(r"fingerprint (\S+)", body) if body is not None else None
    return m.group(1) if m else None


DEFAULT_FINGERPRINT_TIMEOUT = 60                         # seconds for one git call — with 8 a large repository could never be measured, so every verdict there was UNKNOWN
FINGERPRINT_TIMEOUT_ENV = "OQA_FINGERPRINT_TIMEOUT"


def fingerprint_timeout():
    """Seconds one git call of the fingerprint may take: the environment variable OQA_FINGERPRINT_TIMEOUT, or the default when it is not set or is not a positive number"""
    try:
        seconds = float(os.environ.get(FINGERPRINT_TIMEOUT_ENV, ""))
    except ValueError:
        return DEFAULT_FINGERPRINT_TIMEOUT
    return seconds if 0 < seconds < float("inf") else DEFAULT_FINGERPRINT_TIMEOUT


def fingerprint(root, timeout=None):
    """Fingerprint of the code on disk — None when it cannot be measured. timeout: seconds per git call (default: fingerprint_timeout())"""
    if timeout is None:
        timeout = fingerprint_timeout()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ, GIT_INDEX_FILE=os.path.join(tmp, "index"))
            def git(*args):
                return subprocess.run(["git", "-C", str(root), "-c", "core.quotepath=false", "-c", "core.autocrlf=false"] + list(args),
                                      capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=timeout)
            # The index starts from the tree of HEAD: `git add -A` into an empty index passes over every path .gitignore names, also a tracked one
            # (a `settings.py` added with `git add -f`), so editing such a file left the fingerprint — and with it all old evidence — unchanged.
            # With the tracked files already in the index, `add -A` updates them like any other tracked file. No commit yet: nothing to start from
            steps = []
            head_tree = git("rev-parse", "-q", "--verify", "HEAD^{tree}")
            if head_tree.returncode not in (0, 1):           # 1 = no commit yet; anything else (not a repository, a damaged one) cannot be measured
                return None
            if head_tree.returncode == 0 and head_tree.stdout.strip():
                steps.append(["read-tree", head_tree.stdout.strip()])
            # The excluded folders are removed in a separate step instead of with an exclude pathspec on `git add` — in a repository whose
            # .gitignore ignores `.oqa/`, merely naming it makes `git add` exit 1 (the tree is right, but it cannot be told from a real failure).
            # So after the start from HEAD: add everything, drop the excluded folders from the index, write the tree
            steps += [["add", "-A", "--", "."], ["rm", "-r", "--cached", "-q", "--ignore-unmatch", "--"] + list(EXCLUDED)]
            for args in steps:
                if git(*args).returncode != 0:
                    return None
            out = git("write-tree")
            return out.stdout.strip()[:12] if out.returncode == 0 and out.stdout.strip() else None
    except (OSError, subprocess.SubprocessError):
        return None


# ───────── Evidence records ─────────
#     python evidence.py fingerprint --repo <root>          fingerprint of the code now (measure it right before starting a review agent)
#     python evidence.py record --repo <root> --kind "Silent Failure Review" --result done --fingerprint <fingerprint at start> --basis "findings 5 · accepted 5"
#                                                           (only the manual checks and the Independent Review Gate — what a tool produces is recorded by that tool)
#     python evidence.py show --repo <root>                 every check compared with the fingerprint now
#
# Judging from run-log timestamps, command text and patterns in the spec text is guessing ("which code is this evidence for · which repository · how is the
# text read"). So every evidence line carries the **code fingerprint**, and Core only asks "is there evidence with the same fingerprint as now".
# - Place: <repo>/.oqa/evidence.jsonl — one per repository (evidence of another repository cannot mix in). .oqa/ is outside the fingerprint, so recording does not change it
# - One line: {time, kind, fingerprint, result, basis, tool, (name), (equivalent)} — kind is the same string as the check name in core.py's rule table
# - When the fingerprint cannot be measured it is "unknown" — that line is evidence for no code at all
# - Changing one line of code changes the fingerprint, so **all** earlier evidence becomes stale — no per-check rules (creation time · pairing · code changed after approval) are needed
# - Why this is not a separate file: the fingerprint and the evidence are one thing
GOOD_RESULTS = ("pass", "approved", "done")
BAD_RESULTS = ("fail", "rejected")
RESULTS = GOOD_RESULTS + BAD_RESULTS + ("unknown",)
# Checks the main conversation records by hand — the code a review or a UI check looked at is the code **at start**, so the fingerprint measured then must be passed
MANUAL_CHECKS = ("Silent Failure Review", "Reasoning Review", "Blind Review", "Fuzz Test (LLM)", "E2E UI Test")
# What the command line (`evidence.py record`) may record: the manual checks and the independent review gate — a person looked, and says so with the
# fingerprint measured at the start. Every other kind is the output of a tool that ran something; typed by hand it would be a `pass` with nothing run
# (six such lines used to give DONE). The tools record through the functions record · record_quietly, which take every kind
HAND_KINDS = MANUAL_CHECKS + ("Independent Review Gate",)
TOOL_KINDS = {"Regression Test": "report.py", "Mutation Test": "report.py", "Requirements Traceability": "report.py", "Verification Report": "report.py",
              "Manual Mutation Test": "manual_mutate.py", "Baseline": "baseline.py"}
# Every "kind" Core looks for: the check names of core.py's rule table (the same strings, in that order) and the verification report. The command line
# refuses any other --kind — a typo was recorded under a name no check has, with exit code 0, and the real check silently stayed missing.
# (core.py imports this module, so the list is written out here; a test compares it with core.RULES so that the two cannot drift)
KNOWN_KINDS = ("Regression Test", "Mutation Test", "Manual Mutation Test", "Requirements Traceability", "E2E UI Test", "Silent Failure Review", "Reasoning Review",
               "Blind Review", "Fuzz Test (LLM)", "Independent Review Gate", "Baseline", "Verification Report")


def evidence_path(repo):
    return Path(repo) / ".oqa" / "evidence.jsonl"


def record(repo, kind, result, basis="", fingerprint_value=None, tool="", **extra):
    """Appends one evidence line and returns it. Without fingerprint_value it is measured now ("unknown" when that fails). ValueError when result is not one of the fixed values"""
    if result not in RESULTS:
        raise ValueError("result must be one of %s: %r" % (" · ".join(RESULTS), result))
    repo = Path(repo).resolve()
    line = {"time": time.strftime("%Y-%m-%dT%H:%M:%S"), "kind": kind, "fingerprint": (fingerprint_value if fingerprint_value else fingerprint(repo)) or "unknown",
            "result": result, "basis": str(basis or "")[:400], "tool": tool}
    line.update({k: v for k, v in extra.items() if v is not None})
    evidence_path(repo).parent.mkdir(exist_ok=True)
    with open(evidence_path(repo), "a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")
    return line


def record_quietly(repo, kind, result, basis="", fingerprint_value=None, tool="", **extra):
    """Called at the end of a tool — failing to record does not change that tool's result or exit code (Core then shows it as "missing")"""
    try:
        return record(repo, kind, result, basis, fingerprint_value, tool, **extra)
    except Exception:                                    # noqa: BLE001
        return None


def read(repo):
    """Evidence lines — broken lines and non-dict lines are skipped. Empty list when the file is missing"""
    try:
        text = evidence_path(repo).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    out = []
    for raw in text.splitlines():
        try:
            d = json.loads(raw)
        except ValueError:
            continue
        if isinstance(d, dict):
            out.append(d)
    return out


def damaged(repo):
    """Number of non-empty lines of the evidence file that are not JSON objects — read() skips them, so the verdict asks about them: a garbled later
    `fail` line must not leave an earlier `pass` standing unnoticed. 0 when the file is missing"""
    try:
        text = evidence_path(repo).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    count = 0
    for raw in text.splitlines():
        if not raw.strip():
            continue
        try:
            whole = isinstance(json.loads(raw), dict)
        except ValueError:
            whole = False
        count += not whole
    return count


def check_evidence(lines, kind, fingerprint_now):
    """(state, basis, the lines of that check with the fingerprint now) — state: ok · failed · unknown · stale · missing.
    Only lines with the fingerprint now are evidence. Lines with a name (individual tests) count by the latest result per name — one bad one means failed (another test's success does not cover it).
    When there is a line without a name (a tool recorded the whole check), the latest such line is the check's result — but a failure of an individual test recorded after it stays a failure.
    ok needs a result that is one of GOOD_RESULTS: a missing or unrecognised result ("skipped") is unknown, and so is a name whose latest result is
    neither good nor bad (the pass of another name, or a whole-check line recorded before it, does not cover a result nobody knows)"""
    of_kind = [d for d in lines if d.get("kind") == kind]
    current = [d for d in of_kind if fingerprint_now and d.get("fingerprint") == fingerprint_now]
    if not current:
        if of_kind:
            d = of_kind[-1]
            return "stale", "%s %s · fingerprint %s (now %s) — evidence for different code" % (d.get("time"), d.get("result"), d.get("fingerprint"), fingerprint_now or "unknown"), []
        return "missing", "no evidence recorded for this check", []
    last, order = {}, {}
    for i, d in enumerate(current):
        last[d.get("name") or ""] = d
        order[d.get("name") or ""] = i
    whole = last.pop("", None)
    bad = [d for d in last.values() if d.get("result") in BAD_RESULTS]
    unsure = [d for d in last.values() if d.get("result") not in GOOD_RESULTS + BAD_RESULTS]
    if whole is not None:
        # Before/after goes by **position in the file**, not by "time" — time has one-second resolution, so a "whole pass → individual fail" written in the same second lost the failure
        later_bad = [d for d in bad if order[d.get("name") or ""] > order[""]]
        if whole.get("result") in BAD_RESULTS or later_bad:
            d = later_bad[-1] if later_bad else whole
            return "failed", "%s %s%s · %s" % (d.get("time"), (str(d.get("name")) + " ") if d.get("name") else "", d.get("result"), d.get("basis") or ""), current
        if whole.get("result") == "unknown":
            return "unknown", "%s unknown · %s" % (whole.get("time"), whole.get("basis") or ""), current
        if whole.get("result") not in GOOD_RESULTS:      # no result, or a word that is not a result ("skipped") — not folded into ok
            return "unknown", "%s result %r is not a known result — taken as unknown · %s" % (whole.get("time"), whole.get("result"), whole.get("basis") or ""), current
        later_unsure = [d for d in unsure if order[d.get("name") or ""] > order[""]]
        if later_unsure:
            d = later_unsure[-1]
            return "unknown", "%s %s %s — recorded after the whole-check line, its result is not known" % (d.get("time"), d.get("name"), d.get("result")), current
        return "ok", "%s %s · %s" % (whole.get("time"), whole.get("result"), whole.get("basis") or ""), current
    if bad:
        d = bad[-1]
        return "failed", "%s %s %s%s" % (d.get("time"), d.get("name"), d.get("result"), (" and %d more" % (len(bad) - 1)) if len(bad) > 1 else ""), current
    good = [d for d in last.values() if d.get("result") in GOOD_RESULTS]
    if good and unsure:
        d = unsure[-1]
        return "unknown", "%s %s %s — its result is not known, and the pass of another name does not cover it" % (d.get("time"), d.get("name"), d.get("result")), current
    if good:
        return "ok", "%s %s and others, %d in all, %s" % (good[-1].get("time"), good[-1].get("name"), len(good), good[-1].get("result")), current
    return "unknown", "there are lines with the fingerprint now, but every result is unknown", current


def main(argv=None):
    import argparse
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    parser = argparse.ArgumentParser(description="Record and show verification evidence together with the code fingerprint")
    parser.add_argument("action", choices=("fingerprint", "record", "show"))
    parser.add_argument("--repo", required=True)
    parser.add_argument("--kind")
    parser.add_argument("--result", choices=RESULTS)
    parser.add_argument("--basis", default="")
    parser.add_argument("--fingerprint", help="fingerprint of the code that evidence looked at — for a review agent, the value measured when it was started")
    args = parser.parse_args(argv)
    repo = Path(args.repo).resolve()
    if args.action == "fingerprint":
        fp = fingerprint(repo)
        print(fp or "unknown")
        return 0 if fp else 2
    if args.action == "record":
        if not args.kind or not args.result:
            print("record needs --kind and --result")
            return 2
        if args.kind not in KNOWN_KINDS:
            print("unknown check name %r — --kind must be one of: %s" % (args.kind, " · ".join(KNOWN_KINDS)))
            return 2
        if args.kind not in HAND_KINDS:
            print("\"%s\" is recorded by %s, not by hand — run that tool; only these can be recorded here: %s" % (args.kind, TOOL_KINDS.get(args.kind, "its tool"), " · ".join(HAND_KINDS)))
            return 2
        if args.fingerprint is None:
            print("\"%s\" needs --fingerprint (the value measured with `evidence.py fingerprint` right before starting or running it) — recording with the fingerprint now would claim it also looked at code changed afterwards" % args.kind)
            return 2
        given = args.fingerprint.strip()
        if not given or given.lower() == "unknown":
            print("the fingerprint given with --fingerprint could not be measured (%r) — nothing is recorded: a line without a fingerprint is evidence for no code at all. Measure it again with `evidence.py fingerprint`" % args.fingerprint)
            return 2
        extra = {}
        if args.kind == "Independent Review Gate":
            # the gate reviewer judges the code against the spec, and the spec lies outside the fingerprint: what the spec promised when the gate
            # answered is stored with the line (as report.py does), so an expected result reworded afterwards does not keep the approval
            import spec as spec_module                   # here, not at the top: spec.py imports this module
            problem = spec_module.current_problem(repo)
            if problem:
                # the gate answered about a spec — when it cannot be told which one, the line would carry no spec hash and the approval would
                # outlive any later change of the expected results
                print("the Independent Review Gate is not recorded: %s" % problem)
                return 2
            spec_path = spec_module.current_spec(repo) if (repo / ".oqa" / "specs").is_dir() else None
            spec_text = spec_module.read_text(spec_path) if spec_path else None
            if spec_text:
                extra["spec_hash"] = spec_hash(spec_text)
        line = record(repo, args.kind, args.result, args.basis, given, "manual", **extra)
        print("recorded: %s · %s · fingerprint %s" % (line["kind"], line["result"], line["fingerprint"]))
        return 0
    now = fingerprint(repo)
    lines = read(repo)
    print("fingerprint now %s · evidence %d lines · %s" % (now or "unknown", len(lines), evidence_path(repo)))
    for kind in sorted({str(d.get("kind")) for d in lines}):
        state, basis, _ = check_evidence(lines, kind, now)
        print("  %-28s %-8s %s" % (kind, state, basis))
    return 0


if __name__ == "__main__":
    sys.exit(main())
