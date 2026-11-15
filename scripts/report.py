# -*- coding: utf-8 -*-
"""**"It is done" as a table counted by the machine — the Verification Report.**

    python report.py --repo . --spec .oqa/specs/2026-01-15_login.md [--range working] [--max N] [--jobs auto] [--stop-over 30] [--yes] [--no-write]

Order
1. impact — only the tests that pass through the changed lines are selected and run. Gaps (no test passed through the changed lines) and unseen files (non-Python) are counted.
   A brand-new test file (`test_*.py` · `*_test.py`) is in no map: the entries of `.oqa/tests.json` whose command names it are run here as well and join the selected tests
2. mutate — the changed lines of the changed code files (test files excluded) are broken to count whether the tests selected in 1 bite
3. trace — for every E number of the spec's "## Expected results", the tests carrying the marker `verifies: <spec>/E<number>` and their pass / fail
4. "### Verification Report" under the spec's "## Results" is written anew (replaced when present). The fingerprint (evidence.py) is written with it —
   when the code changes afterwards, core.py reports that the report is stale. The evidence line of the report also names the spec file
   (key `spec`) — when the current spec is another one, core.py reports that the report was made for another spec — and carries a short hash of
   the spec's "## Expected results" and "## Finish line" (key `spec_hash`): when those change after the report, core.py asks for a new report

The verdict is never reduced — when any of the three is unknown it is written as "unknown", not flattened to "pass".
A gap of "added lines" is lifted only when the mutants inside that line range were killed, or when mutate.py found nothing to mutate in it and no
mutant was left out by `--max` (gap_states); when mutants on those lines survived, the survivors are what is listed, not a second "gap" entry.
The same evidence settles the other two gaps the map can never close: a new source file (every mutant generated in it killed), and changed lines of an
existing source file that no recorded test passed through — the test for them was added with the change — when mutants ran on those lines, none was
left out by `--max` and every one was killed (a kill is proof that a test executes the line and checks it). With zero mutants, a survivor, a crashed
mutant or a sample, nothing is lifted.
A new test file is not a gap to cover: listed in `.oqa/tests.json` it is confirmed by its own passing run; not listed, the report says so and how to add
the entry, and is "cannot judge" on that account. A new file under a tests folder that carries no test name (`tests/helpers.py`) is support code:
it is mutated like a new source file and confirmed by the same rule; otherwise its gap says what it is taken for and what to do.
An expected result one of whose tagged files is run by no listed test is "not run" (cannot judge), and its row names that file.
Equivalent mutants declared in the spec are taken out of the incomplete list (mark_equivalents), and changed code with 0 generated mutants is "cannot judge".
Also "cannot judge", never "all passed": no test was selected for the change · fewer mutants were run than generated (`--max`) · one changed code file
gave no mutant while others did · a changed non-Python file that is not known to be text · a target file mutate.py could not read as Python ·
a mutation run stopped by `--stop-over` (its low time estimate is over the limit).
When the change holds no Python source to mutate (only test files, text, or deleted files) the Mutation Test evidence is `done` with that basis —
a finish line that names Mutation Test can then be met; it is not a `pass` of a run.
Nothing under .oqa/ counts as a changed or an unseen file.
Exit code: 0 all passed · 1 something incomplete · 2 cannot judge
"""
import argparse
import json
import re
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import traceability                               # noqa: E402
import mutate                                     # noqa: E402
import impact                                     # noqa: E402
from evidence import REPORT_HEADING, fingerprint, section, record_quietly, spec_hash, is_text_file   # noqa: E402 — the same definitions core.py uses ("plain text file" is evidence.is_text_file: one list for the plan and the report)
from init import is_test_name                     # noqa: E402 — the naming rule the test list is built from (`test_*.py` · `*_test.py`)

# A new file with a test name that no entry of the test list runs: it was never run, so nothing can be said about it — and "gap" would send the reader
# looking for coverage of a file that only needs an entry
UNLISTED_TEST = "%s looks like a test file that is not listed in .oqa/tests.json — add an entry for it (or run `init.py --force`) and run report.py again: a test that is not listed is never run"
NOTHING_TO_MUTATE = "no changed Python source to mutate (only test files, text files or deleted files changed) — no mutant was run"
TEST_FILE = re.compile(r"(^|/)tests?/|_test\.py$|(^|/)test_[^/]*\.py$")
# An equivalent mutant declared in the spec (a mutant whose behaviour is the same after the change, so no test can catch it). It is read only with a reason
#   - equivalent mutant: scripts/a.py:132 True ↔ False — that value is set again on the next line and never used
EQUIVALENT_PATTERN = re.compile(r"^\s*[-*]\s*equivalent mutant:\s*(\S+):(\d+)\s+(.+?)\s+—\s+(\S.*)$", re.M)
ADDED_LINES = re.compile(r"\d+ added line")      # the gap reason impact.py gives to lines added after recording
CHANGED_LINES = "no test passed through changed lines"      # the gap reason impact.py gives to changed lines of an existing file that no recorded test executed
CHANGED_BASIS = "changed lines no recorded test passed through"      # how the report names those lines once other evidence settled them
# A new file under a tests folder that carries no test name and was not confirmed: the gap says what the file is taken for, why it is open, and what to do
SUPPORT_FILE = ("new file under a tests folder that is not named like a test (test_*.py · *_test.py) — taken as support code for the tests and mutated like a new source file:"
                " confirmed only when every mutant generated in it is killed; %s. If it is a test, rename it and list it in .oqa/tests.json")
TRACE_UNKNOWN = ("no number", "duplicate number", "not run", "unknown")      # verdicts of traceability.py that mean "cannot judge" — neither a pass nor something found wrong


def read_equivalents(spec_text):
    """[(file, line, kind, reason)] — what the spec declares as "- equivalent mutant: file:line kind — reason" """
    return [(m.group(1).replace("\\", "/"), int(m.group(2)), m.group(3).strip(), m.group(4).strip()) for m in EQUIVALENT_PATTERN.finditer(spec_text or "")]


def mark_equivalents(mutation, equivalents):
    """Among the survived mutants, the verdict of those the spec declares equivalent (file · line · kind all equal) becomes "equivalent" and the counts are corrected. Killed mutants and other lines are not touched.
    Only when that (file · line · kind) has **exactly one survived mutant** — when one line has two mutants of the same kind (the two numbers of `x == 1 and y == 2`) it is not known which one was declared, so nothing is taken out
    (a one-line declaration would take out a real hole as well). A declared line that matches no mutant stays in mutation["equivalent_unmatched"] and is shown in the report (a stale declaration whose line has shifted)"""
    table = {(file, line, kind): reason for file, line, kind, reason in equivalents}
    survived = {}
    for row in (mutation.get("mutants") or []) if table else []:
        if row.get("verdict") == "survived":
            survived.setdefault((row.get("file"), row.get("line"), row.get("kind")), []).append(row)
    unmatched = []
    for key, reason in table.items():
        matched = survived.get(key, [])
        if len(matched) == 1:
            matched[0].update(verdict="equivalent", equivalent_reason=reason)
            mutation["counts"]["survived"] = mutation["counts"].get("survived", 0) - 1
            mutation["counts"]["equivalent"] = mutation["counts"].get("equivalent", 0) + 1
        else:
            unmatched.append("%s:%d %s — %s" % (key + ("not excluded: %d survived mutants of the same line and kind" % len(matched) if matched else "no matching survived mutant",)))
    if unmatched:
        mutation["equivalent_unmatched"] = unmatched


def new_test_files(selected_result):
    """The gap files of the impact result that are a brand-new test file: reported as "new file" and carrying a test name (init.is_test_name)"""
    return [file for file, reason in (selected_result.get("gaps") or {}).items() if str(reason).startswith("new file") and is_test_name(file)]


def new_support_files(selected_result):
    """The gap files of the impact result that are a brand-new file under a tests folder without a test name (`tests/helpers.py`, `tests/conftest.py`):
    support code the tests use. It is not a test (nothing runs it by itself) and it is in no map, so the only thing that can show that a test executes
    it and checks what it does is a mutant of it being killed — collect hands such a file to the mutation run like a new source file"""
    return [file for file, reason in (selected_result.get("gaps") or {}).items() if gap_kind(file, reason) == "new support file"]


def gap_kind(file, reason):
    """What kind of gap impact.py reported for that file — the kinds other evidence can settle, or None for the rest:
    "new test file" · "new support file" (new, under a tests folder, no test name) · "new file" (new source) · "test file" (added lines in an existing
    file under a tests folder or with a test name) · "added lines" (source) · "changed lines" (source: no recorded test passed through the changed lines)"""
    reason, test_side = str(reason), bool(TEST_FILE.search(file))
    if reason.startswith("new file"):
        return "new test file" if is_test_name(file) else "new support file" if test_side else "new file"
    if ADDED_LINES.match(reason):
        return "test file" if test_side else "added lines"
    return "changed lines" if reason.startswith(CHANGED_LINES) and not test_side else None


def run_new_tests(repo, selected_result, tests):
    """Run the tests of a brand-new test file and add them to the impact result (selected · results · seconds · total).
    The map is recorded before such a file exists, so the map can never select it and no recorded test passes through it: left to the map, the file
    stayed a gap whatever was done. A new test file is a test, not code to be covered — what can be said about it is whether it runs and passes.
    So every entry of the test list whose command names the file (traceability.tests_for: the path or its module form as a whole word) is run here
    once, by the same yardstick as the selected tests, and joins them: the mutants and the trace then use it like any other selected test.
    A new test file that no entry names is left alone — gap_states reports it as "not listed" """
    map_now = impact.read_map(repo)
    mapped = set((map_now or {}).get("tests") or {})
    by_name = {t["name"]: t for t in tests}
    selected = selected_result.setdefault("selected", {})
    results, seconds = selected_result.setdefault("results", {}), selected_result.setdefault("seconds", {})
    def run_listed(name, why):
        if name in results:
            return
        started = time.time()
        code, output = impact.run(by_name[name]["command"], repo)
        results[name] = "fail" if code is None or code != 0 or re.search(by_name[name].get("fail_pattern") or mutate.DEFAULT_FAIL_PATTERN, output or "") else "pass"
        seconds[name] = round(time.time() - started, 1)
        print("  %-14s %s %5.1fs  (%s)" % (name, results[name], seconds[name], why))

    for file in new_test_files(selected_result):
        for name in traceability.tests_for(tests, file, None, repo):
            if name not in selected and name not in mapped:
                selected_result["total"] = (selected_result.get("total") or 0) + 1      # "selected 2 / total 1" otherwise: the total counts the recorded tests
            selected[name] = sorted(set(selected.get(name) or []) | {file})
            run_listed(name, "new test file %s — not in the map, run as listed" % file)
    # Every other listed test the map does not know: a case added to an existing case file, an entry that was renamed, an entry added by hand for
    # a file that already existed. The map cannot select it for any line, so it was never run when only test files changed (with source changes
    # the mutation baseline ran it) — a new failing case then ended in "all passed". Only when something changed: with no change nothing is run
    for name in (sorted(by_name) if selected_result.get("changed") and map_now is not None else []):
        if name in mapped or name in results:
            continue
        if name not in selected:
            selected_result["total"] = (selected_result.get("total") or 0) + 1
        selected.setdefault(name, [])
        run_listed(name, "listed but not in the map — added or renamed after the map was recorded, run as listed")


def collect(repo, spec, range_="working", max_=30, timeout=300, jobs=1, stop_over=None):
    """The tests run on the original only once — (1) the results of the impact tests are taken over by (2) the mutation baseline and (4) the trace, and when the map is not stale
    mutation runs, for each mutant, only the tests that passed through that line (otherwise one report ran the same tests many times over)"""
    started = time.time()
    start_fingerprint = fingerprint(repo)                    # the code before the tests run — when it differs from the fingerprint afterwards, the code changed while they ran
    head_now = impact.head(repo)
    result = {"time": time.strftime("%Y-%m-%d %H:%M"), "HEAD": (head_now or "?")[:7], "head": head_now, "range": range_}
    with tempfile.TemporaryDirectory() as tmp:
        impact_path = Path(tmp) / "impact.json"
        code = impact.select(repo, range_, True, mutate.DEFAULT_FAIL_PATTERN, str(impact_path))
        selected_result = json.loads(impact_path.read_text(encoding="utf-8")) if impact_path.is_file() else None
        if selected_result is not None:
            # nothing under .oqa/ is a changed or an unseen file (impact.select leaves them out; a result that still lists them is cleaned here)
            for key in ("changed", "unseen"):
                selected_result[key] = [f for f in selected_result.get(key) or [] if not f.startswith(".oqa/")]
        result["impact"] = selected_result if selected_result is not None else {"unknown": "no impact map — run `impact.py record` first" if code == 2 else "no impact result"}
        # the code to mutate: changed Python source — and a new support file under a tests folder (no test name), which nothing else can settle
        support = new_support_files(selected_result or {})
        code_files = [f for f in (selected_result or {}).get("changed", []) if f.endswith(".py") and (not TEST_FILE.search(f) or f in support) and (repo / f).is_file()]
        # a non-Python file that is not known to be text (.js · .go · Dockerfile · no suffix ...) is code the machine did not see — it is counted as unknown, not flattened to "text edit"
        result["outside_code"] = [f for f in (selected_result or {}).get("unseen", []) if not is_text_file(f)]
        tests = impact.test_list(repo)
        if selected_result is not None:
            run_new_tests(repo, selected_result, tests)
        selected = sorted((selected_result or {}).get("selected", {}))
        listed = {t["name"]: t["command"] for t in tests}
        commands = [listed[name] for name in selected if name in listed]
        if selected_result is None:
            result["mutation"] = {"unknown": "no impact result, so the tests to select are unknown"}
        elif not code_files:
            result["mutation"] = {"none": "no changed Python code (only test files · text edits — non-Python code is counted separately)"}
        elif not commands:
            result["mutation"] = {"unknown": "no test passes through the changed code, so there is no test to run mutants with (gap)"}
        else:
            mutation_path = Path(tmp) / "mutation.json"
            args = ["--repo", str(repo), "--files", *code_files, "--range", range_, "--max", str(max_), "--timeout", str(timeout), "--json", str(mutation_path), "--jobs", str(jobs)]
            for command in commands:
                args += ["--test", command]
            tests_path, baseline_path = Path(tmp) / "tests.json", Path(tmp) / "baseline.json"
            # the seconds measured during the impact tests are handed over — mutation runs fast tests first and stops on a kill
            tests_path.write_text(json.dumps([dict(t, seconds=(selected_result.get("seconds") or {}).get(t["name"])) for t in impact.test_list(repo)], ensure_ascii=False), encoding="utf-8")
            baseline_path.write_text(json.dumps(selected_result.get("results", {}), ensure_ascii=False), encoding="utf-8")
            args += ["--tests-file", str(tests_path), "--baseline-results", str(baseline_path)]
            if not selected_result.get("map_stale") and range_ == "working":       # a stale map is not used per line — every selected test, as before
                args += ["--map", str(repo / ".oqa" / "impact-map.json")]
            if stop_over is not None:
                args += ["--stop-over", str(stop_over)]
            mutate.main(args)
            result["mutation"] = json.loads(mutation_path.read_text(encoding="utf-8")) if mutation_path.is_file() else {"unknown": "no mutation result"}
            if result["mutation"].get("stopped"):
                # stopped before any mutant ran — the low time estimate is over the limit. Nothing was measured, so it is unknown (never "generated 0" or a pass)
                result["mutation"] = {"unknown": "mutants were not run — %s. Run report.py again with --yes to go ahead" % result["mutation"]["stopped"],
                                      "baseline_results": result["mutation"].get("baseline_results") or {}}
            elif result["mutation"].get("baseline") == "not run" and result["mutation"].get("basis"):
                # mutate.py could not read a target file as Python (not UTF-8 · a syntax error) — no mutant was built for any file. That is unknown with its reason, not "generated 0 — no node to mutate"
                result["mutation"] = {"unknown": "mutants were not run — %s" % result["mutation"]["basis"]}
            elif not result["mutation"].get("generated") and result["mutation"].get("generated_by_file") and set(result["mutation"]["generated_by_file"]) <= set(result["mutation"].get("inert_files") or []):
                # every changed Python file is inert as a whole (an empty `__init__.py`, imports and docstrings only): there is nothing a mutant
                # could break — the same as "no changed Python code", not "generated 0" (which no test could ever turn into a result)
                result["mutation"] = {"none": "the changed Python source holds nothing to mutate (%s: only imports · docstrings · bare definitions, or added lines of that kind)" % ", ".join(sorted(result["mutation"]["generated_by_file"])),
                                      "inert_files": result["mutation"]["inert_files"], "baseline_results": result["mutation"].get("baseline_results") or {},
                                      # kept: gap_states lifts an "added lines" gap only when mutate.py named every added hunk as holding nothing to mutate
                                      "added_without_mutants": result["mutation"].get("added_without_mutants") or {}}
            if result["mutation"].get("counts"):
                # equivalent lines are read only from the "## Results" section — so that a line of the same shape in explanations or examples does not become an exemption (the same reason the verdict line is read only inside the section)
                mark_equivalents(result["mutation"], read_equivalents(section(spec.read_text(encoding="utf-8-sig"), "Results") or ""))
    # (4) trace — takes over the results that (1) and the mutation baseline obtained on the original (so the same test is not run again)
    known = dict((result.get("impact") or {}).get("results") or {})
    known.update((result.get("mutation") or {}).get("baseline_results") or {})
    result["spec"] = traceability.trace(repo, spec, run_tests=True, known_results=known)
    end_fingerprint = fingerprint(repo)                      # measured after all tests ran — files the tests leave behind are included, so the report is not stale the moment it is written
    # when it differs from the start, it is unknown which code this result belongs to — recording the end fingerprint would turn a pass of the code before the edit into evidence for the edited code
    # the fingerprint is used only when start and end were **both measured and equal** — when the start could not be measured and only the end can, it is unknown whether the code changed in between
    result["fingerprint_changed"] = bool(start_fingerprint and end_fingerprint and start_fingerprint != end_fingerprint)
    result["fingerprint"] = end_fingerprint if start_fingerprint and end_fingerprint and start_fingerprint == end_fingerprint else None
    impact_runs = len((result.get("impact") or {}).get("results") or {})
    result["runs"] = {"impact": impact_runs, "mutation": (result.get("mutation") or {}).get("test_runs", 0), "trace": result["spec"].get("tests_run", 0),
                      "seconds": round(time.time() - started, 1)}
    result["runs"]["total"] = result["runs"]["impact"] + result["runs"]["mutation"] + result["runs"]["trace"]
    return result


def confirmed_gaps(result):
    """{file: basis} — the "added lines" gaps for which other evidence confirms that tests pass through them.
    Added lines appear after the map was recorded and are not in it → always a gap, so the report could never come out as "all passed". Therefore
    · code file: at least one mutant was run **inside the added line range** and all were killed (those declared equivalent left out) — a test that does not pass through that line cannot kill it.
      Mutants on other (edited) lines of the same file are not counted — one such kill would lift the gap of a new 50-line function without a test.
      When mutants were thinned out by `--max` and an added hunk has no mutant that was run (it did not fall into the sample), it stays a gap — raise `--max` and run again
    · code file, no mutant inside the added ranges at all: confirmed when mutate.py states that every one of those ranges holds nothing to mutate
      (`added_without_mutants`) and no mutant was left out by `--max` — the basis then reads "added lines with nothing to mutate: <lines>"
    · test file: there are selected tests that pass through that file and all passed — test code is executed by the test that runs it
    · new test file (a test name, reported as "new file"): the listed tests whose command names it all passed — its own run
    · new code file, and a new file under a tests folder that carries no test name (support code, mutated like a source file): at least one mutant
      of that file ran and all were killed, none left out by `--max`
    · changed lines of an existing code file that no recorded test passed through: the same — at least one mutant ran on them (mutate.py builds
      mutants on changed lines only), all were killed, none left out by `--max`. Zero mutants, a survivor, a crashed mutant, a sample: not lifted
    Such a gap of a test file, and files whose added line range was not received, stay gaps (what could not be confirmed is not hidden)"""
    return {file: basis for file, (state, basis) in gap_states(result).items() if state == "confirmed"}


def lines_text(ranges):
    """`1, 6-9` for [[1, 1], [6, 9]]"""
    return ", ".join(str(first) if first == last else "%d-%d" % (first, last) for first, last in ranges)


def nothing_to_mutate(result, file):
    """The added line ranges of that gap file in which mutate.py found no candidate mutant at all (`added_without_mutants` of its result) —
    only ranges impact.py reported as added too, so the two tools agree on which lines are meant"""
    selected_result, mutation = result.get("impact") or {}, result.get("mutation") or {}
    quiet = [list(r) for r in (mutation.get("added_without_mutants") or {}).get(file) or []]
    return [list(r) for r in (selected_result.get("added_ranges") or {}).get(file) or [] if list(r) in quiet]


def gap_states(result):
    """{file: (state, basis)} for every gap impact.py reported — state: "confirmed" (not counted) · "survived" · "unlisted" · "failing" · "open".
    Only "added lines" gaps, a new file (which is added lines as a whole), changed lines of a code file no recorded test passed through, and a new
    test file can leave "open"; see confirmed_gaps for the rule. One reasoning for the three code cases: a killed mutant proves a test runs the line.
    · a new file with a test name (`test_*.py` · `*_test.py`): confirmed when the listed tests that run it all passed (its own run — see run_new_tests);
      "failing" when one of them did not pass (the verdict lists that test, not a second "gap" entry); "unlisted" when no entry of the test list runs
      it — the basis says to add an entry, and the verdict is "cannot judge" (neither a pass nor a gap that more coverage could close)
    · confirmed, code file: mutants ran inside the added ranges and all were killed — or no mutant ran there and mutate.py says that every added range
      holds nothing to mutate (an `import`, a comment, a blank line, a docstring: no mutant can ever fall into such a hunk, so "add a test and run
      again" could never lift it). Neither holds when mutants were thinned out by `--max`
    · survived: a mutant inside the added ranges (on the changed lines · in the new file) survived. The cause is that survivor, which the verdict
      lists as "survived mutants" — the file is labelled so and not listed a second time as a gap
    · open: everything else (the basis is impact.py's reason; for a new support file under a tests folder it is SUPPORT_FILE with what was missing)"""
    selected_result, mutation = result.get("impact") or {}, result.get("mutation") or {}
    states, new_tests = {}, new_test_files(selected_result)
    for file, reason in (selected_result.get("gaps") or {}).items():
        states[file] = ("open", str(reason))
        if file in new_tests:
            # a brand-new test file: there is nothing to "cover" it — it is confirmed by its own run (collect ran the listed tests whose command names it)
            touching = sorted(name for name, files in (selected_result.get("selected") or {}).items() if file in files)
            if not touching:
                states[file] = ("unlisted", UNLISTED_TEST % file)
            elif all((selected_result.get("results") or {}).get(name) == "pass" for name in touching):
                states[file] = ("confirmed", "new test file — its own run passed (%s)" % ", ".join(touching))
            else:
                states[file] = ("failing", "new test file — its own run did not pass")
            continue
        kind = gap_kind(file, reason)
        # a new code file has no lines in the map and was always a gap (the map is recorded before the edit) — the whole file is treated as "added lines" and confirmed by the same rule:
        # only when that file has at least one mutant, all were killed, and nothing was thinned out. A new support file under a tests folder likewise
        new_file, support = kind in ("new file", "new support file"), kind == "new support file"
        # changed lines of an existing source file that no recorded test executed (the test for them was added with the change, after the map was
        # recorded): the same reasoning — a kill is proof that a test executes the line and checks it. mutate.py builds mutants on changed lines only,
        # so every mutant of that file lies on them
        changed = kind == "changed lines"
        if kind is None:
            continue
        if kind == "test file":
            touching = [name for name, files in (selected_result.get("selected") or {}).items() if file in files]
            if touching and all((selected_result.get("results") or {}).get(name) == "pass" for name in touching):
                states[file] = ("confirmed", "test file — %d test(s) passing through it passed" % len(touching))
            continue
        ranges = [[1, 10 ** 9]] if new_file or changed else (selected_result.get("added_ranges") or {}).get(file) or []

        def inside(row, first, last):
            """Does the mutant lie on those lines — its node from its first to its last line (a statement that runs over an added line counts)"""
            line = row.get("line")
            return isinstance(line, int) and line <= last and ((row.get("spot") or {}).get("end_line") or line) >= first
        rows = [row for row in mutation.get("mutants", []) if row.get("file") == file and row.get("verdict") != "equivalent" and any(inside(row, first, last) for first, last in ranges)]
        touched = [(first, last) for first, last in ranges if any(inside(row, first, last) for row in rows)]
        survived = sum(1 for row in rows if row.get("verdict") == "survived")
        # when nothing was thinned out every generated mutant was run, so a hunk without a mutant is lines with no node to mutate (comments · text · def headers) — it does not block
        # when thinned out nothing is confirmed at all — even in a hunk a mutant reached, or in a new file, some mutants may not have been run (the result does not say which were dropped).
        # The intended way is to give `--max` a value larger than the number generated so that all of them run
        thinned = (mutation.get("generated") or 0) > len(mutation.get("mutants") or [])
        quiet = nothing_to_mutate(result, file)
        if survived:
            states[file] = ("survived", "%d survived" % survived)
        elif thinned:
            pass
        elif rows and all(row.get("verdict") == "killed" for row in rows):
            states[file] = ("confirmed", ("new support file for tests (not named like a test) — all %d mutant(s) of that file killed" % len(rows)) if support else
                            ("new file — all %d mutant(s) of that file killed" % len(rows)) if new_file else
                            ("%s — all %d mutant(s) on them killed" % (CHANGED_BASIS, len(rows))) if changed else
                            "all %d mutant(s) on the added lines killed — mutants in %d of %d added hunk(s)" % (len(rows), len(touched), len(ranges)))
        elif not rows and ranges and not new_file and not changed and len(quiet) == len(ranges):
            states[file] = ("confirmed", "added lines with nothing to mutate: %s" % lines_text(ranges))
        elif not rows and new_file and file in (mutation.get("inert_files") or []):
            # an empty `__init__.py`, or a new file of imports and docstrings only: no line of it can be executed by a test or broken by a mutant
            states[file] = ("confirmed", "new file with nothing to mutate (empty, or only imports · docstrings · bare definitions)")
        if support and states[file][0] == "open":
            # still a gap — but one that says what the file is taken for and why it is not confirmed, not a bare "no test passed through it"
            states[file] = ("open", SUPPORT_FILE % ("mutants were left out by --max, so not all of them ran" if thinned else "no mutant was run in it" if not rows
                                                    else "not every mutant in it was killed"))
    return states


def lines_word(result, file):
    """"changed" for a gap of changed lines no recorded test passed through, "added" for the others (added lines · a new file, which is added lines as a whole)"""
    return "changed" if gap_kind(file, ((result.get("impact") or {}).get("gaps") or {}).get(file)) == "changed lines" else "added"


def not_run_text(item):
    """` — tagged file(s) …` for an expected result that carries its tag in a file no listed test runs (traceability.py: `not_run_files`), else an empty text"""
    files = item.get("not_run_files") or []
    return (" — tagged file(s) no test listed in .oqa/tests.json runs: %s (list them, or remove the tag)" % ", ".join(files)) if files else ""


def unmeasured_mutation(mutation):
    """[(reason for the verdict, short basis for the evidence line)] — what a mutation run that did run left unmeasured. Empty when every changed line was tested.
    · sampled: fewer mutants were run than generated (`--max`) — the mutants that were not run say nothing, so "all run mutants killed" is not "all killed"
    · a target file without any mutant while the run had some (only deleted lines · a def header): the tests were never asked about that file's change"""
    found = []
    generated, run = mutation.get("generated") or 0, len(mutation.get("mutants") or [])
    if generated > run:
        found.append(("only %d of %d mutants were run (--max) — not all changed lines were tested" % (run, generated), "only %d of %d mutants were run (--max)" % (run, generated)))
    for file, count in sorted((mutation.get("generated_by_file") or {}).items()):
        if not count and file not in (mutation.get("inert_files") or []):      # a file that is inert as a whole has nothing to verify by mutation
            found.append(("no mutant could be generated for %s — its change is not verified by mutation" % file, "no mutant for %s" % file))
    return found


def judge(result):
    """(exit code, incomplete list, unknown list)"""
    incomplete, unknown = [], []
    if result.get("fingerprint_changed"):
        unknown.append("the code changed while the tests were running — which code this result belongs to is unknown (run again)")
    elif "fingerprint" in result and result["fingerprint"] is None:
        unknown.append("could not measure the code fingerprint (start or end) — which code this result belongs to is unknown")
    selected_result = result["impact"]
    if "unknown" in selected_result:
        unknown.append(selected_result["unknown"])
    else:
        # a gap whose added lines carry survived mutants is not listed here: the survivors are listed below ("survived mutants <n>") — one problem, one entry
        incomplete += ["gap %s" % file for file, (state, _) in gap_states(result).items() if state == "open"]
        incomplete += ["test %s %s" % (name, value) for name, value in selected_result.get("results", {}).items() if value != "pass"]
        # a new test file without an entry in the test list was never run: unknown, with what to do about it
        unknown += [basis for _, (state, basis) in gap_states(result).items() if state == "unlisted"]
        if selected_result.get("map_stale"):
            unknown.append("the impact map is stale, so selected tests and gaps were matched against old lines — record again")
        if not selected_result.get("results"):
            # not a single test was run for this change — "no test failed" is then no evidence at all (the Regression Test evidence line says unknown for the same reason)
            unknown.append("no test was selected for this change")
    if result.get("outside_code"):
        unknown.append("%d non-Python code file(s) not seen by the machine (%s)" % (len(result["outside_code"]), ", ".join(result["outside_code"][:4])))
    mutation = result["mutation"]
    if "unknown" in mutation:
        unknown.append(mutation["unknown"])
    elif mutation.get("baseline") == "fail":
        unknown.append("mutation baseline (original) failed — %s" % (mutation.get("basis") or "reason unknown")[:120])
    elif "none" not in mutation and not mutation.get("generated"):
        # there is changed code but not a single mutant could be generated (deleted lines · def headers only) — whether the tests bite is unknown. Left as a pass, Core would count Mutation as "ok"
        unknown.append("there is changed code but no mutant could be generated (generated 0) — whether the tests catch that change is unknown")
    elif "none" not in mutation:
        counts = mutation.get("counts") or {}
        if counts.get("survived"):
            incomplete.append("survived mutants %d" % counts["survived"])
        if counts.get("crashed"):
            unknown.append("crashed mutants %d — whether a test caught them is unknown" % counts["crashed"])
        if counts.get("gap"):
            incomplete.append("mutants no test passed through %d" % counts["gap"])      # a gap is not a pass
        unknown += [reason for reason, _ in unmeasured_mutation(mutation)]
    traced = result["spec"]
    if traced["status"] != "present":
        unknown.append("expected results of the spec: %s" % traced["status"])
    else:
        incomplete += ["%s %s" % (item["id"], item["verdict"]) for item in traced["items"] if item["verdict"] not in ("pass",) + TRACE_UNKNOWN]
        # an item one of whose tagged files was not run says which file — the tests that did run say nothing about that file
        unknown += ["expected result \"%s\": %s%s" % (item["text"][:40], item["verdict"], not_run_text(item)) for item in traced["items"] if item["verdict"] in TRACE_UNKNOWN]
    return (1 if incomplete else 2 if unknown else 0), incomplete, unknown


def check_results(result):
    """{check name: (result, basis)} — one evidence value for each check that the report itself ran. The result is pass · fail · unknown.
    Core does not read the report in the spec text; it matches this evidence against the fingerprint now. When there was no Python source to mutate
    ("none": only test files · text · deleted files changed) the Mutation line is `done` with that basis — the check is satisfied (there is nothing
    it could look at), but it is not the `pass` of a run. Without that line a finish line naming Mutation Test could never be met for such a change"""
    code, incomplete, unknown = judge(result)
    selected_result, mutation, traced = result["impact"], result["mutation"], result["spec"]
    out = {"Verification Report": ({0: "pass", 1: "fail", 2: "unknown"}[code],
                                   "all passed" if code == 0 else "incomplete %d — %s" % (len(incomplete), "; ".join(incomplete)[:200]) if code == 1 else "cannot judge — " + "; ".join(unknown)[:200])}
    if "unknown" in selected_result or selected_result.get("map_stale") or not selected_result.get("results"):
        out["Regression Test"] = ("unknown", selected_result.get("unknown") or ("impact map stale" if selected_result.get("map_stale") else "0 tests selected — no test was run"))
    else:
        states = gap_states(result)
        # added lines whose mutants survived: nothing confirms that a test passes through them, so this is not a pass either — said with its cause
        bad = (["%s %s" % pair for pair in selected_result["results"].items() if pair[1] != "pass"] + ["gap %s" % file for file, (state, _) in states.items() if state == "open"]
               + ["%s lines not confirmed %s (mutants on them survived)" % (lines_word(result, file), file) for file, (state, _) in states.items() if state == "survived"])
        unlisted = [basis for _, (state, basis) in states.items() if state == "unlisted"]
        # a new test file that no entry of the test list runs: the tests that ran say nothing about it — never a pass, and not a failure of something that ran
        out["Regression Test"] = (("fail", "; ".join(bad)[:200]) if bad else ("unknown", "; ".join(unlisted)[:200]) if unlisted
                                  else ("pass", "%d selected tests passed · gaps 0" % len(selected_result["results"])))
    if "none" in mutation:
        out["Mutation Test"] = ("done", NOTHING_TO_MUTATE)
    else:
        counts = mutation.get("counts") or {}
        if "unknown" in mutation or mutation.get("baseline") == "fail" or not mutation.get("generated") or counts.get("crashed"):
            out["Mutation Test"] = ("unknown", mutation.get("unknown") or ("baseline failed" if mutation.get("baseline") == "fail" else "generated 0" if not mutation.get("generated") else "crashed %d" % counts["crashed"]))
        elif unmeasured_mutation(mutation):
            # a sampled run, or a changed file without any mutant — the mutants that ran say nothing about the rest, so this is never recorded as a pass
            out["Mutation Test"] = ("unknown", "; ".join(short for _, short in unmeasured_mutation(mutation))[:200])
        elif counts.get("survived") or counts.get("gap"):
            out["Mutation Test"] = ("fail", "survived %d · gap %d" % (counts.get("survived", 0), counts.get("gap", 0)))
        else:
            out["Mutation Test"] = ("pass", "run %d · killed %d%s" % (len(mutation.get("mutants", [])), counts.get("killed", 0), (" · equivalent %d" % counts["equivalent"]) if counts.get("equivalent") else ""))
    if traced["status"] != "present":
        out["Requirements Traceability"] = ("unknown", "expected results of the spec: %s" % traced["status"])
    else:
        verdicts = [item["verdict"] for item in traced["items"]]
        bad = [v for v in verdicts if v not in ("pass",) + TRACE_UNKNOWN]
        out["Requirements Traceability"] = (("fail", "not passed %d" % len(bad)) if bad else ("unknown", "unknown result of a verifying test") if "unknown" in verdicts
                                            else ("unknown", "no number · not run") if any(v != "pass" for v in verdicts) or not verdicts
                                            else ("pass", "E items %d passed" % len(verdicts)))
    return out


def report_text(result):
    code, incomplete, unknown = judge(result)
    lines = [REPORT_HEADING, "",
             "counted by report.py · %s · HEAD %s · range %s · fingerprint %s" % (result["time"], result["HEAD"], result["range"], result["fingerprint"] or "unknown"), ""]
    lines.append("**Verdict: %s**" % ("all passed" if code == 0 else "incomplete %d" % len(incomplete) if code == 1 else "cannot judge"))
    for entry in incomplete + ["unknown: " + u for u in unknown]:
        lines.append("- " + entry)
    lines += ["", "| Expected result | Verifying tests | Verdict |", "|---|---|---|"]
    traced = result["spec"]
    if traced["status"] != "present":
        lines.append("| — | — | %s |" % traced["status"])
    for item in traced["items"]:
        # a tagged file that no listed test runs is shown next to the tests that did run — the row must not read as if the whole item had been checked
        shown_tests = " · ".join(part for part in (", ".join(item["tests"]), ("not run: " + ", ".join(item["not_run_files"])) if item.get("not_run_files") and item["tests"] else "") if part)
        lines.append("| %s %s | %s | %s |" % (item["id"], item["text"].replace("|", "/")[:70], shown_tests or "—", item["verdict"]))
    selected_result = result["impact"]
    lines.append("")
    if "unknown" in selected_result:
        lines.append("- Impact tests: unknown — %s" % selected_result["unknown"])
    else:
        passed = sum(1 for value in selected_result.get("results", {}).values() if value == "pass")
        lines.append("- Impact tests: changed files %d · selected tests %d / total %d · passed %d%s" % (
            len(selected_result.get("changed", [])), len(selected_result.get("selected", {})), selected_result.get("total", 0), passed, " · map stale" if selected_result.get("map_stale") else ""))
        states = gap_states(result)
        confirmed = {file: basis for file, (state, basis) in states.items() if state == "confirmed"}
        with_survivors = {file: basis for file, (state, basis) in states.items() if state == "survived"}
        lines.append("- Gaps: %s" % ("; ".join("%s (%s)" % (file, basis) for file, (state, basis) in states.items() if state == "open") or "none"))
        not_listed = [file for file, (state, _) in states.items() if state == "unlisted"]
        if not_listed:
            # not a gap either: the file is a test that was never run because the test list has no entry for it (what to do is in the unknown line above)
            lines.append("- Test files not listed in .oqa/tests.json (not run): %s" % ", ".join(not_listed))
        # changed lines no recorded test passed through are settled by the same evidence as added lines and new files (killed mutants) — on lines of their own, named as what they are
        for word in ("added", "changed"):
            # not a gap to close with more coverage: a test does run there, and a mutant on those lines got past it (listed under Mutation below)
            some =["%s (%s)" % pair for pair in with_survivors.items() if lines_word(result, pair[0]) == word]
            if some:
                lines.append("- %s lines not confirmed — mutants on them survived: %s" % (word.capitalize(), "; ".join(some)))
        some = ["%s (%s)" % (file, basis) for file, basis in confirmed.items() if lines_word(result, file) == "added"]
        if some:
            lines.append("- Added lines · new files — confirmed by other evidence: %s" % "; ".join(some))
        some = ["%s (%s)" % (file, basis[len(CHANGED_BASIS) + 3:]) for file, basis in confirmed.items() if lines_word(result, file) == "changed"]
        if some:
            lines.append("- %s — confirmed by other evidence: %s" % (CHANGED_BASIS.capitalize(), "; ".join(some)))
        quiet = {file: nothing_to_mutate(result, file) for file in states}
        if any(quiet.values()):
            lines.append("- Added lines with nothing to mutate: %s" % "; ".join("%s %s" % (file, lines_text(ranges)) for file, ranges in quiet.items() if ranges))
        # Which changed Python files were left out of the mutation run because of their name or folder. The rule is by name only (`test_*.py`,
        # `*_test.py`, a `tests/` folder): a real module called `speed_test.py` is on this line too, and this is the one place that says so
        by_name = sorted(f for f in selected_result.get("changed", []) if f.endswith(".py") and TEST_FILE.search(f) and f not in new_support_files(selected_result))
        if by_name:
            lines.append("- Test code by its name or folder — not mutated: %s%s" % (", ".join(by_name[:8]), " ..." if len(by_name) > 8 else ""))
        lines.append("- Unseen (non-Python): %d — of which code %d" % (len(selected_result.get("unseen", [])), len(result.get("outside_code", []))))
    mutation = result["mutation"]
    if "unknown" in mutation or "none" in mutation:
        lines.append("- Mutation: %s" % (mutation.get("unknown") or mutation.get("none")))
    elif mutation.get("baseline") == "fail":
        lines.append("- Mutation: baseline (original) failed — not counted")
    elif not mutation.get("generated"):
        lines.append("- Mutation: generated 0 — no node to mutate on the changed lines (deleted lines · def headers only, etc.). This does not mean \"no weak tests\"")
    else:
        counts = mutation.get("counts", {})
        methods = {}
        for row in mutation.get("mutants", []):
            methods[row.get("selection", "all")] = methods.get(row.get("selection", "all"), 0) + 1
        lines.append("- Mutation (changed lines): generated %d · run %d · killed %d · survived %d · crashed %d · gap %d%s — test selection %s" % (
            mutation.get("generated", 0), len(mutation.get("mutants", [])), counts.get("killed", 0), counts.get("survived", 0), counts.get("crashed", 0), counts.get("gap", 0),
            (" · equivalent (declared in the spec) %d" % counts["equivalent"]) if counts.get("equivalent") else "",
            " · ".join("%s %d" % pair for pair in sorted(methods.items())) or "—"))
        # crashed mutants are shown line by line too — which mutant crashed and why must be seen to tell whether a test caught it. Equivalent ones come with their reason
        shown = [row for row in mutation.get("mutants", []) if row["verdict"] in ("survived", "gap", "crashed", "equivalent")]
        for row in [r for r in shown if r["verdict"] == "equivalent"] + [r for r in shown if r["verdict"] != "equivalent"][:16]:      # equivalent ones first and all of them, so they are not cut off
            extra = row.get("equivalent_reason") if row["verdict"] == "equivalent" else str(row.get("basis") or "").replace("\n", " ")[:90] if row["verdict"] == "crashed" else ""
            if row["verdict"] == "survived" and row.get("mutated_line"):
                extra = "mutated line: `%s`" % row["mutated_line"]        # which value on the line was changed — the kind alone does not say
            lines.append("  - %s %s:%d %s%s" % (row["verdict"], row["file"], row["line"], row["kind"], (" — " + extra) if extra else ""))
        if len([r for r in shown if r["verdict"] != "equivalent"]) > 16:
            lines.append("  - … and %d more line(s) (all in the mutate.py --json result)" % (len([r for r in shown if r["verdict"] != "equivalent"]) - 16))
        for entry in mutation.get("equivalent_unmatched") or []:
            lines.append("  - equivalent line of the spec not matched: %s" % entry)
    runs = result.get("runs")
    if runs:
        lines.append("- Test runs: %d (impact tests %d · mutation %d · trace %d) · elapsed %.0fs" % (
            runs["total"], runs["impact"], runs["mutation"], runs["trace"], runs["seconds"]))
    lines.append(REPORT_END)
    return "\n".join(lines) + "\n"


# The last line of the report. The next run replaces the old report up to this line only: without it everything down to the next heading was
# taken for the old report, so an `- equivalent mutant: …` line or review notes written below the report were deleted by the following run
REPORT_END = "<!-- end of the verification report — what is written below this line is kept -->"


def write_into(spec, text):
    original = spec.read_text(encoding="utf-8-sig")
    heading = re.search(r"^## Results[^\n]*\n", original, re.M | re.I)      # any letter case, as evidence.section reads it — else a second "## Results" would be appended after `## results`
    if not heading:
        return original.rstrip("\n") + "\n\n## Results\n\n" + text
    after = original[heading.end():]
    following = re.search(r"^## ", after, re.M)              # end of the "## Results" section — even when another section follows, the report goes in front of it
    section_end = following.start() if following else len(after)
    body, rest = after[:section_end], after[section_end:]
    # the old report ends at its end line; a report written before that line existed runs to the next heading or the end, as it always did
    old = re.search(r"^%s\n.*?(?:^%s\n?|(?=^#{2,3} |\Z))" % (re.escape(REPORT_HEADING), re.escape(REPORT_END)), body, re.M | re.S)
    if old:
        body = body[:old.start()] + text + ("\n" if body[old.end():].strip() else "") + body[old.end():]
    else:
        body = body.rstrip("\n") + ("\n\n" if body.strip() else "\n") + text
    return original[:heading.end()] + body + ("\n" if rest and not body.endswith("\n\n") else "") + rest


def spec_key(repo, spec):
    """The spec file as the evidence line names it: its path relative to the repository with forward slashes (the same text as core.json's
    spec_file), or the path as it is when the file lies outside the repository"""
    try:
        return Path(spec).resolve().relative_to(Path(repo).resolve()).as_posix()
    except ValueError:
        return str(spec)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Combine impact tests, mutation and requirement tracing and write the Verification Report")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--spec", required=True)
    parser.add_argument("--range", default="working")
    parser.add_argument("--max", type=mutate.max_arg, default=1000000, help="most mutants to run (1 or more) — by default all of them; with a smaller number an evenly spread sample runs, the report says generated N · run M, and the verdict is \"cannot judge\" (the mutants that were not run say nothing)")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--jobs", type=mutate.jobs_arg, default="auto", help="in how many copies mutants run at the same time — a number, or auto (default: half the CPUs, at most 8)")
    parser.add_argument("--stop-over", type=float, default=30, help="minutes (default 30) — when even the low time estimate of the mutation run is longer, stop before running the mutants and say so; the report is then \"cannot judge\"")
    parser.add_argument("--yes", action="store_true", help="run the mutants however long the estimate is")
    parser.add_argument("--no-write", action="store_true", help="do not write into the spec file, only print")
    args = parser.parse_args(argv)
    repo = Path(args.repo).resolve()
    spec = Path(args.spec)
    spec = spec if spec.is_absolute() else repo / spec
    if not spec.is_file():
        print("no spec file:", spec)
        return 2
    try:
        spec.read_text(encoding="utf-8-sig")
    except (UnicodeDecodeError, OSError) as error:
        # a spec saved as UTF-16 or in a legacy code page: its sections cannot be read and the report cannot be written back into it — said here
        # in one line (it used to end in a traceback half-way through the run)
        print("cannot read the spec as UTF-8 text: %s (%s) — save the file as UTF-8 and run report.py again" % (spec, error.__class__.__name__))
        return 2
    # what the spec promises as this run starts (its expected results and its finish line) — stored with the report evidence, so that core.py can
    # tell when they were changed afterwards. Taken before the checks run: an item added while they run is not covered by them either
    promised = spec_hash(spec.read_text(encoding="utf-8-sig", errors="replace"))
    result = collect(repo, spec, args.range, args.max, args.timeout, args.jobs, None if args.yes else args.stop_over)
    text = report_text(result)
    print("\n" + text)
    if not args.no_write:
        spec.write_text(write_into(spec, text), encoding="utf-8")
        print("→", spec)
        # evidence — one line per check, with the fingerprint measured after all tests ran. The report written into the spec is the copy people read; Core looks at these lines.
        # When the fingerprint could not be measured it is recorded as "unknown" and is evidence for no code at all
        equivalent_count = ((result.get("mutation") or {}).get("counts") or {}).get("equivalent") or None
        # the Verification Report line also says which spec the report was made for — the fingerprint leaves .oqa/ out, so without it a report made
        # for one spec stayed valid evidence after the current spec was switched to another (core.py verdict compares the two)
        for kind, (value, basis) in check_results(result).items():
            report_line = kind == "Verification Report"
            record_quietly(repo, kind, value, basis, result["fingerprint"] or "unknown", "report.py", equivalent=equivalent_count if report_line else None,
                           spec=spec_key(repo, spec) if report_line else None, spec_hash=promised if report_line else None,
                           # the commit the change was measured against: the fingerprint is of the files only, so after `git reset --soft` (a bigger
                           # uncommitted change, the same files) this report still matched the code — Core compares this value with HEAD now
                           head=result.get("head") if report_line else None)
    return judge(result)[0]


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
