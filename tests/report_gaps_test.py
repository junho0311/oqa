# -*- coding: utf-8 -*-
"""**The decisions of report.py that no other test pinned down — each one asserted on its exact observable result.**

    python tests/report_gaps_test.py        (from the repository root)

Most checks hand `judge` / `check_results` / `report_text` / `write_into` a result dictionary built by hand.
`collect` is driven with stand-ins for its four collaborators (impact select, test list, mutation run, trace) so that
every branch is reached in milliseconds; one real git repository without an impact map checks the same path end to end.
"""
import _harness  # noqa: F401,E402 — collects the temp folders in one folder for this test and removes it at the end (first)
import contextlib
import io
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
scripts = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(scripts))
import report  # noqa: E402

failures = []


def check(name, condition, text=""):
    print(("  ok  " if condition else "  FAIL  ") + name)
    if not condition:
        failures.append(name)
        if text != "":
            print("        " + str(text)[:700].replace("\n", " / "))


def safe(function, *args, **kwargs):
    """The value of the call, or a text naming the exception — so a broken decision shows as a failed check of a value, not as a crash of this file"""
    try:
        return function(*args, **kwargs)
    except Exception as error:                            # noqa: BLE001
        return "EXCEPTION %r" % (error,)


def quiet(function, *args, **kwargs):
    """(value, printed text)"""
    printed = io.StringIO()
    with contextlib.redirect_stdout(printed):
        value = safe(function, *args, **kwargs)
    return value, printed.getvalue()


def item(number, verdict, tests=("A",)):
    return {"id": number, "text": "text of " + number, "tests": list(tests), "files": [], "verdict": verdict}


def base(**over):
    """A result where everything passed and there was no Python code to mutate — each check changes one part of it"""
    result = {"time": "T", "HEAD": "abc1234", "range": "working", "fingerprint": "fp1",
              "impact": {"changed": ["a.py"], "selected": {"A": ["a.py"]}, "total": 5, "gaps": {}, "unseen": [], "results": {"A": "pass"}},
              "mutation": {"none": "no changed Python code"},
              "spec": {"status": "present", "items": [item("E1", "pass")]}}
    result.update(over)
    return result


def row(verdict, line=1, file="scripts/a.py"):
    return {"file": file, "line": line, "kind": "x", "verdict": verdict, "basis": ""}


def lines_of(result):
    text = safe(report.report_text, result)
    return text.splitlines()


# ── judge ──
print("judge")
check("everything passed → (0, [], [])", safe(report.judge, base()) == (0, [], []), safe(report.judge, base()))
no_map = base(impact={"unknown": "no map here"})
check("an unknown impact result puts its reason into the unknown list — (2, [], [reason])", safe(report.judge, no_map) == (2, [], ["no map here"]), safe(report.judge, no_map))

# ── check_results: the Verification Report line ──
print("check_results")
got = safe(report.check_results, base())
check("Verification Report — code 0 is (pass, \"all passed\")", isinstance(got, dict) and got.get("Verification Report") == ("pass", "all passed"), got)
failed = base(impact=dict(base()["impact"], results={"A": "fail", "B": "error"}))
got = safe(report.check_results, failed)
check("Verification Report — code 1 is (fail, \"incomplete 2 — <the entries>\")",
      isinstance(got, dict) and got.get("Verification Report") == ("fail", "incomplete 2 — test A fail; test B error"), got)
got = safe(report.check_results, no_map)
check("Verification Report — code 2 is (unknown, \"cannot judge — <the reasons>\")",
      isinstance(got, dict) and got.get("Verification Report") == ("unknown", "cannot judge — no map here"), got)

# the Regression Test line when it is unknown: the reason is the impact reason, else the stale map, else "0 tests selected"
check("Regression Test — an unknown impact result gives (unknown, that reason)", isinstance(got, dict) and got.get("Regression Test") == ("unknown", "no map here"), got)
got = safe(report.check_results, base(impact=dict(base()["impact"], map_stale=True)))
check("Regression Test — a stale map gives (unknown, \"impact map stale\") even when the selected tests passed",
      isinstance(got, dict) and got.get("Regression Test") == ("unknown", "impact map stale"), got)
got = safe(report.check_results, base(impact=dict(base()["impact"], results={})))
check("Regression Test — no test run gives (unknown, \"0 tests selected — no test was run\")",
      isinstance(got, dict) and got.get("Regression Test") == ("unknown", "0 tests selected — no test was run"), got)
got = safe(report.check_results, base())
check("Regression Test — the selected test passed and there is no gap → (pass, \"1 selected tests passed · gaps 0\")",
      isinstance(got, dict) and got.get("Regression Test") == ("pass", "1 selected tests passed · gaps 0"), got)

# the Mutation Test line when it is unknown: four different reasons, each with its own basis
for name, mutation, expected in (
        ("its own unknown reason", {"unknown": "mutants were not run"}, "mutants were not run"),
        ("a failed baseline", {"baseline": "fail", "basis": "A failed", "generated": 3, "counts": {}, "mutants": []}, "baseline failed"),
        ("generated 0", {"baseline": "pass", "generated": 0, "mutants": []}, "generated 0"),
        ("crashed mutants", {"baseline": "pass", "generated": 2, "counts": {"killed": 0, "crashed": 2}, "mutants": [row("crashed"), row("crashed", 2)]}, "crashed 2")):
    got = safe(report.check_results, base(mutation=mutation))
    check("Mutation Test — %s gives (unknown, \"%s\")" % (name, expected), isinstance(got, dict) and got.get("Mutation Test") == ("unknown", expected), got)
got = safe(report.judge, base(mutation={"baseline": "fail", "basis": "A failed", "generated": 3, "counts": {}, "mutants": []}))
check("judge — a failed mutation baseline is cannot judge with its basis", got == (2, [], ["mutation baseline (original) failed — A failed"]), got)

# Requirements Traceability: fail > unknown (an item not run · no item at all) > pass
for name, verdicts, expected in (
        ("one item passed and one was not run", ["pass", "not run"], ("unknown", "no number · not run")),
        ("an item without a number", ["no number"], ("unknown", "no number · not run")),
        ("no item at all", [], ("unknown", "no number · not run")),
        ("every item passed", ["pass", "pass"], ("pass", "E items 2 passed")),
        ("one item failed", ["pass", "fail", "not run"], ("fail", "not passed 1"))):
    traced = {"status": "present", "items": [item("E%d" % (n + 1), v) for n, v in enumerate(verdicts)]}
    got = safe(report.check_results, base(spec=traced))
    check("Requirements Traceability — %s → %s" % (name, expected), isinstance(got, dict) and got.get("Requirements Traceability") == expected, got)

# ── what was not measured is never "all passed" ──
print("unknown is not a pass")


def killed_shape(run, generated, **more):
    """Every mutant that was run was killed — `run` of `generated`"""
    counts = {"killed": run, "survived": 0, "crashed": 0, "gap": 0}
    return base(mutation=dict({"baseline": "pass", "generated": generated, "counts": counts, "mutants": [row("killed", line=n + 1) for n in range(run)]}, **more))


SAMPLED = "only 4 of 10 mutants were run (--max) — not all changed lines were tested"
got = safe(report.judge, killed_shape(4, 10))
check("a sampled run (4 of 10 generated mutants run, all killed) is cannot judge — (2, [], [\"only 4 of 10 mutants were run (--max) …\"]), not all passed", got == (2, [], [SAMPLED]), got)
got = safe(report.check_results, killed_shape(4, 10))
check("a sampled run — Mutation Test is (unknown, \"only 4 of 10 mutants were run (--max)\") and the Verification Report is unknown",
      isinstance(got, dict) and got.get("Mutation Test") == ("unknown", "only 4 of 10 mutants were run (--max)") and got.get("Verification Report") == ("unknown", "cannot judge — " + SAMPLED), got)
got = lines_of(killed_shape(4, 10))
check("a sampled run — the report text says cannot judge and lists the reason", "**Verdict: cannot judge**" in got and "- unknown: " + SAMPLED in got, got)
sampled_survivor = killed_shape(4, 10)
sampled_survivor["mutation"]["mutants"][0]["verdict"] = "survived"
sampled_survivor["mutation"]["counts"].update(killed=3, survived=1)
got = safe(report.judge, sampled_survivor)
check("a sampled run with a survived mutant is incomplete (1) and still carries the sampling as unknown", got == (1, ["survived mutants 1"], [SAMPLED]), got)
got = (safe(report.judge, killed_shape(4, 4)), safe(report.check_results, killed_shape(4, 4)))
check("when every generated mutant was run and killed (4 of 4) it is all passed · Mutation Test (pass, \"run 4 · killed 4\")",
      got[0] == (0, [], []) and isinstance(got[1], dict) and got[1].get("Mutation Test") == ("pass", "run 4 · killed 4"), got)

# one changed code file gave no mutant while another gave some (only deleted lines · a def header): its change was not tested by mutation
QUIET = "no mutant could be generated for scripts/quiet.py — its change is not verified by mutation"
one_quiet = killed_shape(2, 2, generated_by_file={"scripts/a.py": 2, "scripts/quiet.py": 0})
got = safe(report.judge, one_quiet)
check("a changed code file without any mutant next to a file with mutants is cannot judge and the file is named — (2, [], [\"no mutant could be generated for scripts/quiet.py …\"])", got == (2, [], [QUIET]), got)
got = safe(report.check_results, one_quiet)
check("… Mutation Test is (unknown, \"no mutant for scripts/quiet.py\")", isinstance(got, dict) and got.get("Mutation Test") == ("unknown", "no mutant for scripts/quiet.py"), got)
got = lines_of(one_quiet)
check("… the report text says cannot judge and names the file", "**Verdict: cannot judge**" in got and "- unknown: " + QUIET in got, got)
got = safe(report.judge, killed_shape(2, 2, generated_by_file={"scripts/a.py": 1, "scripts/b.py": 1}))
check("when every changed code file gave at least one mutant nothing is added (all passed)", got == (0, [], []), got)

# no test was selected or run for the change
NO_TEST = "no test was selected for this change"
untested = base(impact=dict(base()["impact"], selected={}, results={}))
got = safe(report.judge, untested)
check("no test selected or run → cannot judge — (2, [], [\"no test was selected for this change\"]), not all passed", got == (2, [], [NO_TEST]), got)
got = safe(report.check_results, untested)
check("… the Verification Report (unknown) agrees with the Regression Test (unknown)", isinstance(got, dict) and got.get("Verification Report") == ("unknown", "cannot judge — " + NO_TEST)
      and got.get("Regression Test") == ("unknown", "0 tests selected — no test was run"), got)
check("… the report text says cannot judge", "**Verdict: cannot judge**" in lines_of(untested) and "- unknown: " + NO_TEST in lines_of(untested), lines_of(untested))

# a test result the trace could not read (traceability gives "unknown") is unknown, not a failure and not a pass
odd_trace = base(spec={"status": "present", "items": [item("E1", "pass"), item("E2", "unknown")]})
got = safe(report.judge, odd_trace)
check("an expected result whose verdict is \"unknown\" is cannot judge — (2, [], [expected result \"text of E2\": unknown])", got == (2, [], ["expected result \"text of E2\": unknown"]), got)
got = safe(report.check_results, odd_trace)
check("… Requirements Traceability is (unknown, \"unknown result of a verifying test\")", isinstance(got, dict) and got.get("Requirements Traceability") == ("unknown", "unknown result of a verifying test"), got)

# ── report_text ──
print("report_text")
whole = safe(report.report_text, base())
expected_text = "\n".join([
    "### Verification Report", "",
    "counted by report.py · T · HEAD abc1234 · range working · fingerprint fp1", "",
    "**Verdict: all passed**", "",
    "| Expected result | Verifying tests | Verdict |", "|---|---|---|",
    "| E1 text of E1 | A | pass |", "",
    "- Impact tests: changed files 1 · selected tests 1 / total 5 · passed 1",
    "- Gaps: none",
    "- Unseen (non-Python): 0 — of which code 0",
    "- Mutation: no changed Python code",
    "<!-- end of the verification report — what is written below this line is kept -->"]) + "\n"
check("the whole text of an all-passed report, line for line (no status row in the table when the expected results are present) — its last line is the end line", whole == expected_text, whole)
got = lines_of(base(impact=dict(base()["impact"], changed=["a.py", "notes.md", "speed_test.py", "tests/helpers.py", "tests/new_helper.py"], gaps={"tests/new_helper.py": "new file — no test passed through it"})))
check("the report names the changed Python files left out of mutation because of their name or folder — a module called speed_test.py is on that line; a new support "
      "file under tests/ (which is mutated) and other files are not", "- Test code by its name or folder — not mutated: speed_test.py, tests/helpers.py" in got, got)
check("… and without such a file the line is not written", not [s for s in lines_of(base()) if s.startswith("- Test code by its name")], lines_of(base()))
got = lines_of(base(spec={"status": "no expected results section", "items": []}))
check("when the spec has no expected results the table carries one row with that status", "| — | — | no expected results section |" in got and "**Verdict: cannot judge**" in got, got)
check("when the expected results are present the table has no status row", not [s for s in lines_of(base()) if s.startswith("| — | — |")], lines_of(base()))
got = lines_of(no_map)
check("an unknown impact result is written as \"- Impact tests: unknown — <reason>\" (and no Gaps line follows)",
      "- Impact tests: unknown — no map here" in got and not [s for s in got if s.startswith("- Gaps:")], got)
mixed = base(impact=dict(base()["impact"], selected={"A": ["a.py"], "B": ["a.py"], "C": ["a.py"]}, results={"A": "pass", "B": "pass", "C": "fail"}))
got = lines_of(mixed)
check("the impact line counts only the tests that passed (2 of 3)", "- Impact tests: changed files 1 · selected tests 3 / total 5 · passed 2" in got, got)
got = lines_of(base(mutation={"unknown": "mutants were not run"}))
check("a mutation result that is only unknown is written with its reason", "- Mutation: mutants were not run" in got, got)
got = lines_of(base(mutation={"none": "no changed Python code"}))
check("a mutation result that is only \"none\" is written with its reason", "- Mutation: no changed Python code" in got, got)
got = lines_of(base(mutation={"baseline": "fail", "basis": "A failed", "generated": 3, "counts": {}, "mutants": []}))
check("a failed baseline is written as \"baseline (original) failed — not counted\"", "- Mutation: baseline (original) failed — not counted" in got, got)
got = lines_of(base(mutation={"baseline": "pass", "generated": 0, "mutants": []}))
check("generated 0 is written with the warning that it does not mean \"no weak tests\"",
      "- Mutation: generated 0 — no node to mutate on the changed lines (deleted lines · def headers only, etc.). This does not mean \"no weak tests\"" in got, got)


def survived_shape(count):
    rows = [row("survived", line=n + 1) for n in range(count)]
    return base(mutation={"baseline": "pass", "generated": count, "counts": {"killed": 0, "survived": count}, "mutants": rows})


got = lines_of(survived_shape(16))
check("exactly 16 survived lines are all shown and nothing is said to be cut", len([s for s in got if s.startswith("  - survived ")]) == 16 and not [s for s in got if "more line(s)" in s], got[-4:])
got = lines_of(survived_shape(17))
check("with 17 survived lines 16 are shown and \"… and 1 more line(s)\" is stated",
      len([s for s in got if s.startswith("  - survived ")]) == 16 and "  - … and 1 more line(s) (all in the mutate.py --json result)" in got, got[-4:])
check("the mutation line of that report counts generated · run · killed · survived · crashed · gap and names the test selection",
      "- Mutation (changed lines): generated 17 · run 17 · killed 0 · survived 17 · crashed 0 · gap 0 — test selection all 17" in got, got)

# ── gaps of added lines: a hunk with nothing to mutate · mutants on the added lines that survived ──
print("added lines — nothing to mutate · survived mutants")
ADDED = "1 added line(s) — added after the map was recorded"


def added_shape(mutant_rows, ranges, quiet=None, generated=None, file="calc.py"):
    """One changed code file with an "added lines" gap: its added ranges, the mutants that ran, and (quiet) the added ranges mutate.py found nothing to mutate in"""
    counts = {k: sum(1 for r in mutant_rows if r["verdict"] == k) for k in ("killed", "survived", "crashed", "gap")}
    mutation = {"baseline": "pass", "generated": len(mutant_rows) if generated is None else generated, "counts": counts, "mutants": mutant_rows}
    if quiet is not None:
        mutation["added_without_mutants"] = {file: quiet}
    return base(impact=dict(base()["impact"], changed=[file], selected={"A": [file]}, gaps={file: ADDED}, added_ranges={file: ranges}), mutation=mutation)


import_only = added_shape([row("killed", 7, "calc.py")], [[1, 1]], quiet=[[1, 1]])
got = safe(report.judge, import_only)
check("an added hunk in which mutate.py found nothing to mutate (`import os` on line 1) is not a gap when every mutant ran: all passed — it used to stay \"gap calc.py\" for ever, whatever the tests did",
      got == (0, [], []), got)
got = safe(report.confirmed_gaps, import_only)
check("… the basis names the lines", got == {"calc.py": "added lines with nothing to mutate: 1"}, got)
got = lines_of(import_only)
check("… and the report says so in its text instead of listing a gap", "- Gaps: none" in got and "- Added lines with nothing to mutate: calc.py 1" in got and "**Verdict: all passed**" in got, got)
got = safe(report.judge, added_shape([row("killed", 7, "calc.py")], [[1, 1]]))
check("without that statement from mutate.py (a result of the old shape) a hunk no mutant ran in stays a gap — nothing says it is empty", got == (1, ["gap calc.py"], []), got)
got = safe(report.judge, added_shape([row("killed", 7, "calc.py")], [[1, 1]], quiet=[[1, 1]], generated=5))
check("when mutants were left out by --max nothing is lifted, also not a hunk with nothing to mutate: the gap stays, next to the sampling unknown",
      isinstance(got, tuple) and got[0] == 1 and got[1] == ["gap calc.py"] and len(got[2]) == 1 and "only 1 of 5 mutants were run" in got[2][0], got)
got = safe(report.judge, added_shape([row("killed", 9, "calc.py")], [[1, 1], [5, 6]], quiet=[[1, 1]]))
check("two added hunks, one with nothing to mutate and one that has candidates but none ran inside it: the gap stays", got == (1, ["gap calc.py"], []), got)
got = safe(report.confirmed_gaps, added_shape([row("killed", 5, "calc.py")], [[1, 1], [5, 6]], quiet=[[1, 1]]))
check("two added hunks, one with nothing to mutate and one whose mutant was killed: confirmed", got == {"calc.py": "all 1 mutant(s) on the added lines killed — mutants in 1 of 2 added hunk(s)"}, got)
spread = dict(row("killed", 4, "calc.py"), spot={"line": 4, "end_line": 6})
got = safe(report.judge, added_shape([spread], [[5, 5]], quiet=[]))
check("a killed mutant of a statement that starts above the added line and runs over it (lines 4 to 6, added line 5) confirms that line", got == (0, [], []), got)

survivor = added_shape([row("survived", 1, "calc.py"), row("killed", 7, "calc.py")], [[1, 2]], quiet=[])
got = safe(report.judge, survivor)
check("mutants on the added lines survived: the survivors are the one thing listed — \"incomplete 1\", not also \"gap calc.py\" (the same problem used to be counted twice, and the reader told to add coverage)",
      got == (1, ["survived mutants 1"], []), got)
got = lines_of(survivor)
check("… the report labels the file \"added lines not confirmed — mutants on them survived\" and does not list it under Gaps",
      "**Verdict: incomplete 1**" in got and "- Gaps: none" in got and "- Added lines not confirmed — mutants on them survived: calc.py (1 survived)" in got, got)
got = safe(report.check_results, survivor)
check("… the Regression Test evidence still fails (nothing confirms that a test passes through those lines) and names that cause, not a plain gap",
      isinstance(got, dict) and got.get("Regression Test") == ("fail", "added lines not confirmed calc.py (mutants on them survived)") and got.get("Mutation Test") == ("fail", "survived 1 · gap 0")
      and got.get("Verification Report") == ("fail", "incomplete 1 — survived mutants 1"), got)
got = safe(report.judge, added_shape([row("crashed", 1, "calc.py")], [[1, 2]], quiet=[]))
check("a crashed mutant on the added lines is not that case: the gap stays a gap", isinstance(got, tuple) and "gap calc.py" in got[1], got)
got = safe(report.judge, base(impact=dict(base()["impact"], changed=["new.py"], gaps={"new.py": "new file — no test passed through it"}),
                              mutation={"baseline": "pass", "generated": 2, "counts": {"killed": 1, "survived": 1}, "mutants": [row("killed", 1, "new.py"), row("survived", 2, "new.py")]}))
check("a new code file whose mutants survived is counted once as well (survived mutants 1, no second \"gap new.py\")", got == (1, ["survived mutants 1"], []), got)

# ── a brand-new test file: listed and run · listed and failing · not listed ──
print("a new test file")
NEW_FILE = "new file — no test passed through it"
EXTRA = "tests/test_extra.py"
UNLISTED = ("tests/test_extra.py looks like a test file that is not listed in .oqa/tests.json — add an entry for it (or run `init.py --force`) and run report.py again:"
            " a test that is not listed is never run")


def new_test_shape(selected, results, file=EXTRA):
    """A changed code file (a.py, test A) and a new file that the map knows nothing about — `selected` and `results` say whether a listed test ran it"""
    return base(impact=dict(base()["impact"], changed=["a.py", file], selected=selected, results=results, gaps={file: NEW_FILE}))


listed_new = new_test_shape({"A": ["a.py"], "extra": [EXTRA]}, {"A": "pass", "extra": "pass"})
got = safe(report.judge, listed_new)
check("a new test file that a listed test ran and that passed is confirmed by its own run: all passed — it used to stay \"gap tests/test_extra.py\" for ever (the map is recorded before the file exists)",
      got == (0, [], []), got)
got = safe(report.confirmed_gaps, listed_new)
check("… the basis names the test that ran it", got == {EXTRA: "new test file — its own run passed (extra)"}, got)
got = safe(report.check_results, listed_new)
check("… the Regression Test evidence is a pass that counts both tests", isinstance(got, dict) and got.get("Regression Test") == ("pass", "2 selected tests passed · gaps 0")
      and got.get("Verification Report") == ("pass", "all passed"), got)
got = lines_of(listed_new)
check("… the report text lists no gap and says what confirmed the file", "**Verdict: all passed**" in got and "- Gaps: none" in got
      and "- Added lines · new files — confirmed by other evidence: tests/test_extra.py (new test file — its own run passed (extra))" in got
      and not [s for s in got if "not listed" in s], got)
failing_new = new_test_shape({"A": ["a.py"], "extra": [EXTRA]}, {"A": "pass", "extra": "fail"})
got = safe(report.judge, failing_new)
check("a new test file whose own run failed is listed once, as that failing test — not also as a gap", got == (1, ["test extra fail"], []), got)
got = safe(report.check_results, failing_new)
check("… and the Regression Test evidence fails with that test", isinstance(got, dict) and got.get("Regression Test") == ("fail", "extra fail"), got)
two_runners = new_test_shape({"A": ["a.py"], "extra": [EXTRA], "extra2": [EXTRA]}, {"A": "pass", "extra": "pass", "extra2": "fail"})
got = (safe(report.confirmed_gaps, two_runners), safe(report.judge, two_runners))
check("when two listed tests run the new file and one of them fails, nothing is confirmed", got == ({}, (1, ["test extra2 fail"], [])), got)

unlisted_new = new_test_shape({"A": ["a.py"]}, {"A": "pass"})
got = safe(report.judge, unlisted_new)
check("a new test file that no listed test runs is cannot judge with a message that says what to do — (2, [], [\"… not listed in .oqa/tests.json — add an entry …\"]), not a bare \"gap tests/test_extra.py\" and not a pass",
      got == (2, [], [UNLISTED]), got)
got = safe(report.check_results, unlisted_new)
check("… the Regression Test evidence is unknown with that message (not pass, not fail) and so is the Verification Report",
      isinstance(got, dict) and got.get("Regression Test") == ("unknown", UNLISTED[:200]) and got.get("Verification Report") == ("unknown", ("cannot judge — " + UNLISTED)[:len("cannot judge — ") + 200]), got)
got = lines_of(unlisted_new)
check("… the report text says cannot judge, gives the message, lists no gap and names the file on its own line",
      "**Verdict: cannot judge**" in got and "- unknown: " + UNLISTED in got and "- Gaps: none" in got
      and "- Test files not listed in .oqa/tests.json (not run): tests/test_extra.py" in got, got)
check("… and it is not among the gaps confirmed by other evidence", safe(report.confirmed_gaps, unlisted_new) == {}, safe(report.confirmed_gaps, unlisted_new))
unlisted_and_failing = new_test_shape({"A": ["a.py"]}, {"A": "fail"})
got = (safe(report.judge, unlisted_and_failing), safe(report.check_results, unlisted_and_failing))
check("an unlisted new test file next to a failing test: incomplete (the failure) and the message stays in the unknown list · the Regression Test evidence fails",
      got[0] == (1, ["test A fail"], [UNLISTED]) and isinstance(got[1], dict) and got[1].get("Regression Test") == ("fail", "A fail"), got)
got = safe(report.judge, new_test_shape({"A": ["a.py"]}, {"A": "pass"}, file="checks/extra_test.py"))
check("the name decides, as in init.py (`test_*.py` · `*_test.py`), not the folder: an unlisted new checks/extra_test.py gets the same message",
      got == (2, [], [UNLISTED.replace(EXTRA, "checks/extra_test.py")]), got)
got = safe(report.judge, new_test_shape({"A": ["a.py"]}, {"A": "pass"}, file="tests/helpers.py"))
check("a new file under tests/ whose name is not a test name (tests/helpers.py) is no test to list: with no mutant run in it, it stays a gap", got == (1, ["gap tests/helpers.py"], []), got)
got = safe(report.judge, base(impact=dict(base()["impact"], changed=["a.py", EXTRA], gaps={EXTRA: "no test passed through changed lines [3]"})))
check("the message is only for a new file: an existing test file with a gap of another kind stays a gap", got == (1, ["gap tests/test_extra.py"], []), got)

# ── changed lines of an existing source file that no recorded test covers, changed together with a new test for it ──
print("changed lines no recorded test passed through — lifted only by killed mutants")
NO_TEST = "no test passed through changed lines [2]"
UTIL_TEST = "tests/test_util.py"
CHANGED_BASIS = "changed lines no recorded test passed through — all %d mutant(s) on them killed"


def changed_shape(mutant_rows, generated=None, reason=NO_TEST, file="util.py"):
    """util.py exists but no recorded test executes it; its line 2 was changed and tests/test_util.py (new, listed, passing) was added — `mutant_rows` are the mutants that ran"""
    counts = {k: sum(1 for r in mutant_rows if r["verdict"] == k) for k in ("killed", "survived", "crashed", "gap")}
    mutation = {"baseline": "pass", "generated": len(mutant_rows) if generated is None else generated, "counts": counts, "mutants": mutant_rows}
    return base(impact=dict(base()["impact"], changed=[file, UTIL_TEST], selected={"util": [UTIL_TEST]}, results={"util": "pass"}, gaps={file: reason, UTIL_TEST: NEW_FILE}), mutation=mutation)


all_killed = changed_shape([row("killed", 2, "util.py"), row("killed", 2, "util.py")])
got = safe(report.judge, all_killed)
check("every mutant on the changed lines was killed and none was left out: the gap is lifted — all passed (it used to stay \"gap util.py\" for ever: the map is recorded before the new test exists)",
      got == (0, [], []), got)
got = safe(report.confirmed_gaps, all_killed)
check("… the basis says what confirmed it and counts the mutants", isinstance(got, dict) and got.get("util.py") == CHANGED_BASIS % 2, got)
got = lines_of(all_killed)
check("… the report text lists no gap and says on its own line that the changed lines were confirmed by other evidence",
      "**Verdict: all passed**" in got and "- Gaps: none" in got
      and "- Changed lines no recorded test passed through — confirmed by other evidence: util.py (all 2 mutant(s) on them killed)" in got
      and "- Added lines · new files — confirmed by other evidence: tests/test_util.py (new test file — its own run passed (util))" in got, got)
got = safe(report.check_results, all_killed)
check("… and the Regression Test evidence is a pass", isinstance(got, dict) and got.get("Regression Test") == ("pass", "1 selected tests passed · gaps 0"), got)
no_mutants = changed_shape([])
got = safe(report.judge, no_mutants)
check("the changed lines produced zero mutants (a comment): nothing was killed, so nothing is lifted — the gap stays, next to \"generated 0\"",
      isinstance(got, tuple) and got[0] == 1 and got[1] == ["gap util.py"] and len(got[2]) == 1 and "generated 0" in got[2][0] and safe(report.confirmed_gaps, no_mutants).get("util.py") is None, got)
got = safe(report.judge, changed_shape([row("killed", 1, "other.py")]))
check("mutants of another file only (none on util.py): the gap stays", isinstance(got, tuple) and got[1] == ["gap util.py"], got)
one_survived = changed_shape([row("killed", 2, "util.py"), row("survived", 2, "util.py")])
got = (safe(report.judge, one_survived), safe(report.confirmed_gaps, one_survived).get("util.py"), safe(report.gap_states, one_survived).get("util.py"))
check("one mutant on the changed lines survived: not lifted — the survivor is the one entry (\"survived mutants 1\"), the file is in state \"survived\" and not confirmed",
      got == ((1, ["survived mutants 1"], []), None, ("survived", "1 survived")), got)
got = lines_of(one_survived)
check("… the report labels the file \"changed lines not confirmed — mutants on them survived\" (they are not added lines)",
      "**Verdict: incomplete 1**" in got and "- Changed lines not confirmed — mutants on them survived: util.py (1 survived)" in got and not [s for s in got if s.startswith("- Added lines not confirmed")], got)
got = safe(report.check_results, one_survived)
check("… and the Regression Test evidence fails naming the changed lines", isinstance(got, dict) and got.get("Regression Test") == ("fail", "changed lines not confirmed util.py (mutants on them survived)"), got)
got = safe(report.judge, changed_shape([row("killed", 2, "util.py"), row("crashed", 2, "util.py")]))
check("a crashed mutant on the changed lines: not lifted — the gap stays", isinstance(got, tuple) and got[1] == ["gap util.py"] and len(got[2]) == 1 and "crashed mutants 1" in got[2][0], got)
got = safe(report.judge, changed_shape([row("killed", 2, "util.py"), row("gap", 2, "util.py")]))
check("a mutant no test passed through on the changed lines: not lifted", isinstance(got, tuple) and "gap util.py" in got[1], got)
got = safe(report.judge, changed_shape([row("killed", 2, "util.py")], generated=5))
check("mutants were sampled (--max, 1 of 5 run): not lifted, although the one that ran was killed", isinstance(got, tuple) and got[1] == ["gap util.py"] and len(got[2]) == 1 and "only 1 of 5 mutants were run" in got[2][0], got)
got = safe(report.judge, changed_shape([dict(row("equivalent", 2, "util.py"), equivalent_reason="r")]))
check("only a mutant declared equivalent (nothing killed): not lifted", isinstance(got, tuple) and "gap util.py" in got[1], got)
got = safe(report.judge, changed_shape([row("killed", 2, "tests/test_old.py")], file="tests/test_old.py"))
check("the rule is for source files: an existing test file with such a gap is not lifted by a row that names it", isinstance(got, tuple) and "gap tests/test_old.py" in got[1], got)

# ── a new file under a tests folder that is not named like a test: support code, settled like a new source file ──
print("a new support file under a tests folder")
HELPERS = "tests/helpers.py"
SUPPORT_OPEN = ("new file under a tests folder that is not named like a test (test_*.py · *_test.py) — taken as support code for the tests and mutated like a new source file:"
                " confirmed only when every mutant generated in it is killed; %s. If it is a test, rename it and list it in .oqa/tests.json")


def support_shape(mutant_rows, generated=None):
    counts = {k: sum(1 for r in mutant_rows if r["verdict"] == k) for k in ("killed", "survived", "crashed", "gap")}
    mutation = {"baseline": "pass", "generated": len(mutant_rows) if generated is None else generated, "counts": counts, "mutants": mutant_rows} if mutant_rows else {"none": "no changed Python code"}
    return base(impact=dict(base()["impact"], changed=["a.py", HELPERS], gaps={HELPERS: NEW_FILE}), mutation=mutation)


helped = support_shape([row("killed", 2, HELPERS), row("killed", 3, HELPERS)])
got = (safe(report.judge, helped), safe(report.confirmed_gaps, helped))
check("every mutant generated in the new support file was killed: confirmed — all passed (it used to stay a bare \"gap tests/helpers.py\" whatever was done)",
      got == ((0, [], []), {HELPERS: "new support file for tests (not named like a test) — all 2 mutant(s) of that file killed"}), got)
got = safe(report.gap_states, support_shape([])).get(HELPERS)
check("no mutant ran in it: it stays a gap whose reason says what the file is taken for, why it is not confirmed and what to do", got == ("open", SUPPORT_OPEN % "no mutant was run in it"), got)
got = lines_of(support_shape([]))
check("… and the Gaps line of the report carries that reason", "- Gaps: tests/helpers.py (%s)" % (SUPPORT_OPEN % "no mutant was run in it") in got, got)
got = safe(report.judge, support_shape([row("killed", 2, HELPERS), row("survived", 3, HELPERS)]))
check("a mutant in it survived: the survivor is the one entry", got == (1, ["survived mutants 1"], []), got)
got = safe(report.gap_states, support_shape([row("killed", 2, HELPERS), row("crashed", 3, HELPERS)])).get(HELPERS)
check("a mutant in it crashed: a gap that says not every mutant was killed", got == ("open", SUPPORT_OPEN % "not every mutant in it was killed"), got)
got = safe(report.gap_states, support_shape([row("killed", 2, HELPERS)], generated=4)).get(HELPERS)
check("mutants were sampled: a gap that says so", got == ("open", SUPPORT_OPEN % "mutants were left out by --max, so not all of them ran"), got)
got = safe(report.new_support_files, {"gaps": {HELPERS: NEW_FILE, "test/data/sample.py": NEW_FILE, EXTRA: NEW_FILE, "src/new.py": NEW_FILE, "tests/old_helpers.py": "2 added line(s) — x"}})
check("new_support_files: new files under tests/ or test/ without a test name — not a new test file, not a new source file, not an existing file", got == [HELPERS, "test/data/sample.py"], got)

# ── an expected result tagged in two files, one of which no listed test runs ──
print("an expected result with a tagged file that was not run")
partly = base(spec={"status": "present", "items": [dict(item("E1", "not run"), files=["tests/test_a.py", "tests/test_b.py"], not_run_files=["tests/test_b.py"]), item("E2", "pass")]})
NOT_RUN_FILE = "expected result \"text of E1\": not run — tagged file(s) no test listed in .oqa/tests.json runs: tests/test_b.py (list them, or remove the tag)"
got = safe(report.judge, partly)
check("judge — the item is cannot judge and the reason names the tagged file that no listed test runs and what to do", got == (2, [], [NOT_RUN_FILE]), got)
got = lines_of(partly)
check("… the traceability row shows the test that ran and the tagged file that was not run, with the verdict \"not run\" — the reader is not told the item is verified",
      "| E1 text of E1 | A · not run: tests/test_b.py | not run |" in got and "| E2 text of E2 | A | pass |" in got and "**Verdict: cannot judge**" in got, got)
got = safe(report.check_results, partly)
check("… and the Requirements Traceability evidence is unknown", isinstance(got, dict) and got.get("Requirements Traceability") == ("unknown", "no number · not run"), got)
traced_repo = Path(tempfile.mkdtemp()) / "traced"
(traced_repo / ".oqa").mkdir(parents=True)
(traced_repo / "tests").mkdir()
(traced_repo / "tests" / "test_a.py").write_text("# verifies: demo/E1\n# verifies: demo/E2\nprint('failures: 0')\n", encoding="utf-8")
(traced_repo / "tests" / "test_b.py").write_text("# verifies: demo/E1\nraise SystemExit(1)\n", encoding="utf-8")
(traced_repo / ".oqa" / "tests.json").write_text(json.dumps([{"name": "a", "command": "python tests/test_a.py"}]), encoding="utf-8")
(traced_repo / "demo.md").write_text("# Spec\n\n## Expected results\n- E1 one\n- E2 two\n\n## Results\n", encoding="utf-8")
got = safe(report.traceability.trace, traced_repo, traced_repo / "demo.md", run_tests=True, known_results={"a": "pass"})
rows_ = [(i["id"], i["tests"], i["verdict"], i.get("not_run_files")) for i in got.get("items", [])] if isinstance(got, dict) else got
check("traceability.trace — E1 is tagged in a listed file that passed and in a file no listed test runs: \"not run\" with that file named, no longer \"pass\" · E2 (tagged only in the listed file) passes",
      rows_ == [("E1", ["a"], "not run", ["tests/test_b.py"]), ("E2", ["a"], "pass", [])], rows_)
got = safe(report.traceability.trace, traced_repo, traced_repo / "demo.md", run_tests=True, known_results={"a": "fail"})
check("… a failing verifying test stays \"fail\" (the worse verdict wins) and the file that was not run is still named",
      isinstance(got, dict) and [(i["verdict"], i.get("not_run_files")) for i in got["items"]][:1] == [("fail", ["tests/test_b.py"])], got)

# core.py plan names the same files before the report runs: changed or new files with a test name that no entry of the test list runs
import core as _plan_core  # noqa: E402
listing = Path(tempfile.mkdtemp()) / "listing"
(listing / ".oqa").mkdir(parents=True)


def plan_file(path, status="new"):
    return {"path": path, "status": status, "new": status == "new", "added": 1, "removed": 0}


changed_files = [plan_file("calc.py", "M"), plan_file("tests/test_extra.py"), plan_file("tests/test_calc.py", "M"), plan_file("checks/more_test.py"),
                 plan_file("tests/helpers.py"), plan_file("tests/test_gone.py", "D"), plan_file("suite/test_folder.py", "M")]
got = safe(_plan_core.unlisted_tests, listing, changed_files)
check("unlisted_tests — without a test list every changed or new file with a test name is named (not the code file, not tests/helpers.py, not the deleted test file)",
      got == ["tests/test_extra.py", "tests/test_calc.py", "checks/more_test.py", "suite/test_folder.py"], got)
(listing / ".oqa" / "tests.json").write_text(json.dumps([{"name": "calc", "command": "python -m pytest tests/test_calc.py -q"}, {"name": "more", "command": "python checks/more_test.py"},
                                                         {"name": "suite", "command": "python -m pytest suite -q"}]), encoding="utf-8")
got = safe(_plan_core.unlisted_tests, listing, changed_files)
check("unlisted_tests — a file an entry's command names is listed · a folder command alone does not say which files it runs", got == ["tests/test_extra.py", "suite/test_folder.py"], got)
(listing / ".oqa" / "impact-map.json").write_text(json.dumps({"tests": {"suite": {"suite/test_folder.py": [1, 2]}}}), encoding="utf-8")
got = safe(_plan_core.unlisted_tests, listing, changed_files)
check("unlisted_tests — a file the impact map recorded for an entry (a command that runs a whole folder) is listed too: only the new test file is left", got == ["tests/test_extra.py"], got)
(listing / ".oqa" / "tests.json").write_text("{not json", encoding="utf-8")
got = safe(_plan_core.unlisted_tests, listing, [plan_file("tests/test_extra.py")])
check("unlisted_tests — a test list that cannot be read has no entry: the file is named, nothing raises", got == ["tests/test_extra.py"], got)
printed_plan = quiet(_plan_core.print_plan, {"created": "T", "HEAD": "abc", "spec_file": None, "files": [], "checks": [], "unlisted_tests": ["tests/test_extra.py", "checks/more_test.py"]})[1]
check("print_plan — one line names them and says what to do",
      [line for line in printed_plan.splitlines() if "not listed" in line] == ["test file(s) not listed in .oqa/tests.json: tests/test_extra.py, checks/more_test.py — add an entry for each (or run `init.py --force`);"
                                                                                " report.py does not run a test that is not listed"], printed_plan)
printed_plan = quiet(_plan_core.print_plan, {"created": "T", "HEAD": "abc", "spec_file": None, "files": [], "checks": [], "unlisted_tests": []})[1]
check("print_plan — no such line when every test file is listed", "not listed" not in printed_plan, printed_plan)

# ── write_into ──
print("write_into")
work = Path(tempfile.mkdtemp())
spec_file = work / "spec.md"
REPORT = "### Verification Report\n\nbody\n"


def written(original, text=REPORT):
    spec_file.write_text(original, encoding="utf-8")
    return safe(report.write_into, spec_file, text)


got = written("# S\n\n## Results\nold line\n\n## Next\nx\n")
check("a section after \"## Results\" — the report goes in front of it with exactly one blank line between",
      got == "# S\n\n## Results\nold line\n\n### Verification Report\n\nbody\n\n## Next\nx\n", repr(got))
got = written("# S\n\n## Results\nold line\n\n## Next\nx\n", REPORT + "\n")
check("when the report text already ends with a blank line no second one is put before the next section",
      got == "# S\n\n## Results\nold line\n\n### Verification Report\n\nbody\n\n## Next\nx\n", repr(got))
got = written("# S\n\n## Results\n")
check("\"## Results\" as the last section — the file ends right after the report (no extra blank line)", got == "# S\n\n## Results\n\n### Verification Report\n\nbody\n", repr(got))
got = written("# S\n\n## Results\nkept\n\n### Verification Report\n\nold body\n\n### Other\nnote\n\n## Next\nx\n")
check("an old report is replaced in place — the lines before it, the sub-section after it and the next section stay",
      got == "# S\n\n## Results\nkept\n\n### Verification Report\n\nbody\n\n### Other\nnote\n\n## Next\nx\n", repr(got))
got = written("# S\n\ntext\n")
check("without a \"## Results\" section one is added at the end", got == "# S\n\ntext\n\n## Results\n\n### Verification Report\n\nbody\n", repr(got))

# ── collect, with stand-ins for impact select · test list · mutation run · trace ──
print("collect")
target = work / "target"
target.mkdir()
(target / "calc.py").write_text("def double(x):\n    return x * 2\n", encoding="utf-8")
target_spec = target / "spec.md"
target_spec.write_text("# Spec\n\n## Expected results\n- E1 x\n\n## Results\n", encoding="utf-8")
TRACED = {"status": "present", "items": [], "tests_run": 3}


def collect_with(impact_result, select_code=1, mutation_result=None, tests=({"name": "A", "command": "python a_test.py"},)):
    """(result of collect, the known results handed to the trace, the arguments handed to the mutation run or None)"""
    seen = {"known": "trace not called", "args": None}

    def fake_select(repo, range_, run_selected, fail_pattern, json_path=None):
        if impact_result is not None:
            Path(json_path).write_text(json.dumps(impact_result), encoding="utf-8")
        return select_code

    def fake_mutate(args):
        seen["args"] = list(args)
        if mutation_result is not None:
            Path(args[args.index("--json") + 1]).write_text(json.dumps(mutation_result), encoding="utf-8")
        return 0

    def fake_trace(repo, spec, run_tests=False, known_results=None, **more):
        seen["known"] = dict(known_results or {})
        return dict(TRACED)

    old = (report.impact.select, report.impact.test_list, report.impact.head, report.mutate.main, report.traceability.trace, report.fingerprint)
    report.impact.select, report.impact.test_list, report.impact.head = fake_select, (lambda repo: [dict(t) for t in tests]), (lambda repo: "0123456789abcdef")
    report.mutate.main, report.traceability.trace, report.fingerprint = fake_mutate, fake_trace, (lambda repo: "fp_same")
    try:
        result = safe(report.collect, target, target_spec)
    finally:
        report.impact.select, report.impact.test_list, report.impact.head, report.mutate.main, report.traceability.trace, report.fingerprint = old
    return (result if isinstance(result, dict) else {"error": result}), seen["known"], seen["args"]


def impact_shape(**over):
    shape = {"map_stale": False, "changed": ["calc.py"], "selected": {"A": ["calc.py"]}, "total": 2, "gaps": {}, "added_ranges": {}, "unseen": [],
             "results": {"A": "pass", "B": "fail"}, "seconds": {"A": 0.1}}
    shape.update(over)
    return shape


got, known, args = collect_with(None, select_code=2)
check("no impact result and exit code 2 from select → impact unknown \"no impact map — run `impact.py record` first\"",
      got.get("impact") == {"unknown": "no impact map — run `impact.py record` first"}, got)
check("no impact result → mutation is unknown \"no impact result, so the tests to select are unknown\" and the mutation run is not started",
      got.get("mutation") == {"unknown": "no impact result, so the tests to select are unknown"} and args is None, (got.get("mutation"), args))
check("no impact result → no code outside Python is counted · impact runs 0 · mutation runs 0 · the trace gets no known result",
      got.get("outside_code") == [] and got.get("runs", {}).get("impact") == 0 and got.get("runs", {}).get("mutation") == 0 and known == {}, (got.get("outside_code"), got.get("runs"), known))
check("HEAD is the first seven characters of the commit id", got.get("HEAD") == "0123456", got.get("HEAD"))
got, known, args = collect_with(None, select_code=1)
check("no impact result with another exit code → impact unknown \"no impact result\"", got.get("impact") == {"unknown": "no impact result"}, got.get("impact"))

unseen = ["notes.md", "app.js", ".oqa/tool.js", "web/Page.HTML", ".oqa/readme.md"]
got, known, args = collect_with(impact_shape(changed=["notes.md", "app.js", "tests/x_test.py", "gone.py"], selected={}, results={}, unseen=unseen))
check("unseen files — every file outside .oqa/ that is not a known text file is counted as code the machine did not see (suffix in any letter case)",
      got.get("outside_code") == ["app.js", "web/Page.HTML"], got.get("outside_code"))
check("only text · test files · deleted files changed → mutation is \"none\" (no changed Python code) and the mutation run is not started",
      got.get("mutation") == {"none": "no changed Python code (only test files · text edits — non-Python code is counted separately)"} and args is None, (got.get("mutation"), args))
judged = safe(report.judge, got)
check("judge of that result — no test was selected and two code files outside Python were not seen: cannot judge, both reasons listed",
      judged == (2, [], ["no test was selected for this change", "2 non-Python code file(s) not seen by the machine (app.js, web/Page.HTML)"]), judged)
# the rule is "text only when known to be text": a file the tool does not know (.go · .rs · Dockerfile · a file without suffix …) is code it did not see
text_files = ["notes.md", "a.txt", "doc/guide.rst", "x.adoc", "img/Logo.PNG", "p.jpg", "p.jpeg", "p.gif", "p.svg", "p.ico", "p.pdf",
              "LICENSE", "NOTICE", "CHANGELOG", "AUTHORS", "sub/.gitattributes", ".editorconfig"]
not_seen = ["main.go", "lib.rs", "src/App.java", "x.c", "tool.rb", "web/page.vue", "nb.ipynb", "setup.ini", "Dockerfile", "Makefile", "bin/tool", "app.js", "data.json", "README", "license.lock",
            ".gitignore", "requirements.txt", "deps/requirements-dev.txt", "constraints.txt"]
got, known, args = collect_with(impact_shape(changed=["calc.py"], results={"A": "pass"}, unseen=text_files + not_seen + [".oqa/tool.go", ".oqa/Makefile"]))
check("known text files (%d suffixes and names) are not counted · every other changed non-Python file (%d: .go · .rs · .java · .c · .rb · .vue · .ipynb · .ini · Dockerfile · Makefile · no suffix …) is code not seen · .oqa/ is left out"
      " — rule changed: a changed `.gitignore` (it can hide a new code file from every tool) and `requirements*.txt` · `constraints*.txt` are not seen either, no longer plain text"
      % (len(text_files), len(not_seen)), got.get("outside_code") == not_seen, got.get("outside_code"))
import core as _core  # noqa: E402 — the plan's kind table and the report must use one definition of "plain text file"
import evidence as _evidence  # noqa: E402
kinds = {name: safe(_core.kind_of, name) for name in text_files + not_seen}
check("core.py's kind table uses the same definition: every file the report passes over as text is kind \"doc\" in the plan, and none of the others is (`.gitignore` used to be text here and \"unknown\" there)",
      all(kinds[name] == "doc" for name in text_files) and not [name for name in not_seen if kinds[name] == "doc"] and kinds[".gitignore"] == "unknown" and kinds["requirements.txt"] == "unknown"
      and report.is_text_file is _evidence.is_text_file and not hasattr(report, "TEXT_NAMES"), kinds)
oqa_shape = impact_shape(changed=["calc.py", ".oqa/tests.json", ".oqa/specs/a.md", ".oqa/hook.js"], results={"A": "pass"}, unseen=[".oqa/tests.json", ".oqa/specs/a.md", ".oqa/hook.js"])
in_oqa, known_, args_ = collect_with(oqa_shape, mutation_result={"baseline": "pass", "generated": 1, "counts": {"killed": 1, "survived": 0, "crashed": 0, "gap": 0}, "mutants": [{"file": "calc.py", "line": 2, "kind": "x", "verdict": "killed"}]})
text_lines = lines_of(dict(in_oqa, spec={"status": "present", "items": [item("E1", "pass")]}))
check("files under .oqa/ are not counted in the report at all: \"changed files 1\" (the plan counts the same one) and \"Unseen (non-Python): 0\" — they used to be counted as 4 changed and 3 unseen",
      "- Impact tests: changed files 1 · selected tests 1 / total 2 · passed 1" in text_lines and "- Unseen (non-Python): 0 — of which code 0" in text_lines and in_oqa.get("outside_code") == [], text_lines)
got["mutation"] = {"none": "no changed Python code"}
judged = safe(report.judge, got)
check("judge of that result — cannot judge, the unknown names how many files were not seen (%d) and the first four" % len(not_seen),
      isinstance(judged, tuple) and judged[0] == 2 and "%d non-Python code file(s) not seen by the machine (main.go, lib.rs, src/App.java, x.c)" % len(not_seen) in judged[2], judged)

got, known, args = collect_with(impact_shape(selected={}, results={}))
check("changed Python code that no test passes through → mutation is unknown \"… no test to run mutants with (gap)\" and the mutation run is not started",
      got.get("mutation") == {"unknown": "no test passes through the changed code, so there is no test to run mutants with (gap)"} and args is None, (got.get("mutation"), args))
got, known, args = collect_with(impact_shape(selected={"ghost": ["calc.py"]}, results={"ghost": "not in list"}))
check("a selected test that is not in the test list gives no command either → the same unknown",
      got.get("mutation") == {"unknown": "no test passes through the changed code, so there is no test to run mutants with (gap)"} and args is None, (got.get("mutation"), args))

stopped = {"baseline": "pass", "stopped": "estimate for 4 mutants: 2 h", "mutants": [], "baseline_results": {"D": "pass"}, "test_runs": 1}
got, known, args = collect_with(impact_shape(), mutation_result=stopped)
check("a mutation run stopped on the estimate → unknown with the reason and how to go ahead · the baseline results it did obtain are kept",
      got.get("mutation") == {"unknown": "mutants were not run — estimate for 4 mutants: 2 h. Run report.py again with --yes to go ahead", "baseline_results": {"D": "pass"}}, got.get("mutation"))
check("the trace takes over the impact results and the kept baseline results of the stopped run", known == {"A": "pass", "B": "fail", "D": "pass"}, known)
check("the mutation run got the changed code file and the command of the selected test", args is not None and args[args.index("--files") + 1] == "calc.py"
      and args[args.index("--test") + 1] == "python a_test.py" and args.count("--test") == 1, args)
check("report.py's docstring no longer promises a stop because \"the run time cannot be estimated\" — every used test is timed in the copy first, so the stop is on the estimate only",
      "cannot be estimated" not in report.__doc__ and "--stop-over" in report.__doc__, report.__doc__[-600:])
# a target file mutate.py could not read as Python: baseline "not run" with a basis — that is not "generated 0, nothing to mutate"
unreadable = {"baseline": "not run", "generated": None, "basis": "calc.py does not parse as Python (invalid syntax, line 1)", "mutants": [], "test_runs": 0, "seconds": 0}
got, known, args = collect_with(impact_shape(results={"A": "pass"}), mutation_result=unreadable)
check("a mutation run that could not read a target file → unknown \"mutants were not run — <file and problem>\"",
      got.get("mutation") == {"unknown": "mutants were not run — calc.py does not parse as Python (invalid syntax, line 1)"}, got.get("mutation"))
got["spec"] = {"status": "present", "items": [item("E1", "pass")]}
judged, text_lines = safe(report.judge, got), lines_of(dict(got, time="T", HEAD="h", range="working"))
check("… judge is cannot judge with that reason · the report text names the file and never says \"generated 0\"",
      judged == (2, [], ["mutants were not run — calc.py does not parse as Python (invalid syntax, line 1)"]) and "**Verdict: cannot judge**" in text_lines
      and "- Mutation: mutants were not run — calc.py does not parse as Python (invalid syntax, line 1)" in text_lines and not [s for s in text_lines if "generated 0" in s], (judged, text_lines))

ran = {"baseline": "pass", "generated": 1, "counts": {"killed": 1, "survived": 0, "crashed": 0, "gap": 0}, "test_runs": 7, "baseline_results": {"D": "pass"},
       "mutants": [{"file": "calc.py", "line": 2, "kind": "x", "verdict": "killed", "tests": ["A"], "ran": 1}]}
got, known, args = collect_with(impact_shape(), mutation_result=ran)
check("the trace takes over the impact results (A · B) and the mutation baseline results (D)", known == {"A": "pass", "B": "fail", "D": "pass"}, known)
check("the run counts — impact = number of impact results (2) · mutation = its test runs (7) · trace = what the trace ran (3) · total 12",
      {k: v for k, v in got.get("runs", {}).items() if k != "seconds"} == {"impact": 2, "mutation": 7, "trace": 3, "total": 12}, got.get("runs"))
check("start and end fingerprint equal → that fingerprint is recorded and nothing is flagged as changed", got.get("fingerprint") == "fp_same" and got.get("fingerprint_changed") is False,
      (got.get("fingerprint"), got.get("fingerprint_changed")))
got, known, args = collect_with(impact_shape(results={}), mutation_result=dict(ran, baseline_results={"A": "pass"}))
check("with no impact result of its own the trace still gets the mutation baseline results · impact runs 0", known == {"A": "pass"} and got.get("runs", {}).get("impact") == 0, (known, got.get("runs")))
got, known, args = collect_with(impact_shape(), mutation_result=dict(ran, baseline_results={}))
check("with no baseline result the trace gets exactly the impact results", known == {"A": "pass", "B": "fail"}, known)

# a new test file is in no map: collect itself runs the listed tests whose command names it, once, and adds them to the selected tests
EXTRA_COMMAND = "python -m pytest tests/test_extra.py -q"
WITH_EXTRA = ({"name": "A", "command": "python a_test.py"}, {"name": "extra", "command": EXTRA_COMMAND})


def collect_new_test(tests, exit_code=0, output="1 passed", gaps=None, results=None):
    """(result of collect, known results handed to the trace, arguments of the mutation run, the commands collect ran itself)"""
    ran_here = []
    old_run = report.impact.run
    report.impact.run = lambda command, repo, *more, **options: (ran_here.append(command), (exit_code, output))[1]
    try:
        shape = impact_shape(changed=["calc.py", EXTRA], results=results or {"A": "pass"}, gaps={EXTRA: NEW_FILE} if gaps is None else gaps)
        return collect_with(shape, mutation_result=dict(ran, baseline_results={}), tests=tests) + (ran_here,)
    finally:
        report.impact.run = old_run


got, known, args, ran_here = collect_new_test(WITH_EXTRA)
check("collect — a new test file with an entry in the test list: that test is run once and joins the selected tests with its result (it used to be left out: the map cannot select a file recorded before it existed)",
      ran_here == [EXTRA_COMMAND] and got.get("impact", {}).get("selected") == {"A": ["calc.py"], "extra": [EXTRA]} and got.get("impact", {}).get("results") == {"A": "pass", "extra": "pass"},
      (ran_here, got.get("impact")))
check("… it is counted in the total (2 recorded + 1 new) and in the impact runs, its result goes to the trace, and the mutants get its command too",
      got.get("impact", {}).get("total") == 3 and got.get("runs", {}).get("impact") == 2 and known == {"A": "pass", "extra": "pass"}
      and args is not None and [args[n + 1] for n, word in enumerate(args) if word == "--test"] == ["python a_test.py", EXTRA_COMMAND], (got.get("impact"), got.get("runs"), known, args))
judged = safe(report.judge, dict(got, spec={"status": "present", "items": [item("E1", "pass")]}))
check("… judge of that result: all passed", judged == (0, [], []), judged)
got, known, args, ran_here = collect_new_test(WITH_EXTRA, exit_code=1)
check("collect — the new test ends with an error: its result is fail", got.get("impact", {}).get("results") == {"A": "pass", "extra": "fail"}, got.get("impact"))
got, known, args, ran_here = collect_new_test(WITH_EXTRA, output="FAILED tests/test_extra.py::test_x")
check("collect — the new test prints a failure line with exit code 0: its result is fail (the same yardstick as the other tests)", got.get("impact", {}).get("results") == {"A": "pass", "extra": "fail"}, got.get("impact"))
got, known, args, ran_here = collect_new_test(WITH_EXTRA, exit_code=None, output="timeout")
check("collect — the new test runs into the time limit: its result is fail", got.get("impact", {}).get("results") == {"A": "pass", "extra": "fail"}, got.get("impact"))
got, known, args, ran_here = collect_new_test(WITH_EXTRA[:1])
judged = safe(report.judge, dict(got, spec={"status": "present", "items": [item("E1", "pass")]}))
check("collect — a new test file without an entry: nothing is run for it, the selected tests stay as they were, and judge gives the \"not listed\" message",
      ran_here == [] and got.get("impact", {}).get("selected") == {"A": ["calc.py"]} and got.get("impact", {}).get("total") == 2 and judged == (2, [], [UNLISTED]), (ran_here, got.get("impact"), judged))
got, known, args, ran_here = collect_new_test(WITH_EXTRA, gaps={EXTRA: "3 added line(s) — added after the map was recorded"})
check("collect — only a new file is run this way: a test file the map already knows (added lines) is left to the map's selection", ran_here == [] and "extra" not in got.get("impact", {}).get("selected", {}),
      (ran_here, got.get("impact")))
got, known, args, ran_here = collect_new_test(WITH_EXTRA, results={"A": "pass", "extra": "pass"})
check("collect — a new test that already has a result is not run a second time, and still joins the selected tests", ran_here == [] and got.get("impact", {}).get("selected") == {"A": ["calc.py"], "extra": [EXTRA]},
      (ran_here, got.get("impact")))
got, known, args, ran_here = collect_new_test(WITH_EXTRA, gaps={"tests/helpers.py": NEW_FILE})
check("collect — a new file whose name is not a test name is not run as a test", ran_here == [], ran_here)
# a listed test the map does not know (a case added to an existing case file · a renamed entry): the map can never select it
old_read_map = report.impact.read_map
try:
    report.impact.read_map = lambda repo: {"tests": {"A": {"calc.py": [1]}}}
    got, known, args, ran_here = collect_new_test(WITH_EXTRA, gaps={}, exit_code=1, output="1 failed")
    judged = safe(report.judge, dict(got, spec={"status": "present", "items": [item("E1", "pass")]}))
    check("collect — a listed test that is not in the map is run although no new file names it, and its failure counts (it used to be left out: a new failing case in a "
          "test-only change ended in \"all passed\")",
          ran_here == [EXTRA_COMMAND] and got.get("impact", {}).get("results") == {"A": "pass", "extra": "fail"} and got.get("impact", {}).get("selected") == {"A": ["calc.py"], "extra": []}
          and got.get("impact", {}).get("total") == 3 and isinstance(judged, tuple) and judged[0] == 1 and "test extra fail" in judged[1], (ran_here, got.get("impact"), judged))
    report.impact.read_map = lambda repo: {"tests": {"A": {"calc.py": [1]}, "extra": {"calc.py": [2]}}}
    got, known, args, ran_here = collect_new_test(WITH_EXTRA, gaps={})
    check("… a listed test the map knows is left to the map's selection", ran_here == [] and "extra" not in got.get("impact", {}).get("selected", {}), (ran_here, got.get("impact")))
finally:
    report.impact.read_map = old_read_map
(target / "tests").mkdir(exist_ok=True)
for name in ("helpers.py", "old_helpers.py", "test_new.py"):
    (target / "tests" / name).write_text("VALUE = 1\n", encoding="utf-8")
support_impact = impact_shape(changed=["calc.py", "tests/helpers.py", "tests/old_helpers.py", "tests/test_new.py"], results={"A": "pass"},
                              gaps={"tests/helpers.py": NEW_FILE, "tests/old_helpers.py": "1 added line(s) — x", "tests/test_new.py": NEW_FILE})
got, known, args = collect_with(support_impact, mutation_result=dict(ran, baseline_results={}))
files_handed = args[args.index("--files") + 1:args.index("--range")] if args else args
check("collect — a new support file under tests/ (no test name) is handed to the mutation run next to the source file; an existing file under tests/ and a new test file are not",
      files_handed == ["calc.py", "tests/helpers.py"], files_handed)
got, known, args = collect_with(impact_shape(changed=["tests/helpers.py"], results={"A": "pass"}, gaps={"tests/helpers.py": NEW_FILE}), mutation_result=dict(ran, baseline_results={}))
check("collect — a change that holds only a new support file is not \"no changed Python code\": the file is mutated", args is not None and "none" not in got.get("mutation", {}), (got.get("mutation"), args))

# ── main ──
print("main")
value, printed = quiet(report.main, ["--repo", str(target), "--spec", "no_such_spec.md"])
check("a spec file that does not exist → exit code 2 and it says so", value == 2 and "no spec file:" in printed, (value, printed))


handed_specs = []                                         # (kind, the `spec` key handed to the evidence record) of the latest main_with call
handed_hashes = []                                        # (kind, the `spec_hash` key) of the same call
handed_heads = []                                         # (kind, the `head` key) of the same call


def main_with(result, *extra, spec_arg="spec.md"):
    """(exit code, printed text, [(kind, value, basis, fingerprint, tool, equivalent)] handed to the evidence record, arguments collect got)"""
    handed, collect_args = [], []
    del handed_specs[:]
    del handed_hashes[:]
    del handed_heads[:]
    target_spec.write_text("# Spec\n\n## Expected results\n- E1 x\n\n## Results\n", encoding="utf-8")
    old_collect, old_quiet = report.collect, report.record_quietly
    report.collect = lambda *a, **k: (collect_args.append(a), result)[1]
    report.record_quietly = lambda *a, **k: (handed_specs.append((a[1], k.get("spec"))), handed_hashes.append((a[1], k.get("spec_hash"))), handed_heads.append((a[1], k.get("head"))),
                                             handed.append(tuple(a[1:6]) + (k.get("equivalent", "not given"),)))[2]
    try:
        value, printed = quiet(report.main, ["--repo", str(target), "--spec", spec_arg] + list(extra))
    finally:
        report.collect, report.record_quietly = old_collect, old_quiet
    return value, printed, handed, collect_args


with_equivalents = base(mutation={"baseline": "pass", "generated": 4, "counts": {"killed": 2, "survived": 0, "crashed": 0, "gap": 0, "equivalent": 2},
                                  "mutants": [row("killed"), row("killed", 2), dict(row("equivalent", 3), equivalent_reason="r1"), dict(row("equivalent", 4), equivalent_reason="r2")]})
value, printed, handed, collect_args = main_with(dict(with_equivalents, head="abc123" * 6 + "abcd"))
check("the report line carries the commit the change was measured against (`head`) — the lines of the single checks do not",
      sorted(handed_heads, key=str) == sorted([("Verification Report", "abc123" * 6 + "abcd")] + [(entry[0], None) for entry in handed if entry[0] != "Verification Report"], key=str), handed_heads)
(target / "wide.md").write_bytes("# Spec\n\n## Expected results\n- E1 x\n\n## Results\n".encode("utf-16"))
value, printed = quiet(report.main, ["--repo", str(target), "--spec", "wide.md"])
check("a spec that is not UTF-8 text (saved as UTF-16) stops report.py with one line and exit code 2 — before anything runs, and the file is left as it is (it used to end in a traceback)",
      value == 2 and "cannot read the spec as UTF-8 text" in printed and "Traceback" not in printed
      and (target / "wide.md").read_bytes() == "# Spec\n\n## Expected results\n- E1 x\n\n## Results\n".encode("utf-16"), (value, printed))
value, printed, handed, collect_args = main_with(with_equivalents)
by_kind = {entry[0]: entry for entry in handed}
check("main writes four evidence lines (one per check) with the result · the basis · the fingerprint of the result · the tool name", value == 0 and len(handed) == 4
      and by_kind.get("Verification Report", ())[:5] == ("Verification Report", "pass", "all passed", "fp1", "report.py")
      and by_kind.get("Mutation Test", ())[:5] == ("Mutation Test", "pass", "run 4 · killed 2 · equivalent 2", "fp1", "report.py"), (value, handed))
check("the number of equivalent mutants (2) goes on the Verification Report line only — the other three lines get none",
      by_kind.get("Verification Report", ())[-1:] == (2,) and [by_kind.get(k, ())[-1:] for k in ("Regression Test", "Mutation Test", "Requirements Traceability")] == [(None,)] * 3, handed)
check("main writes the report into the \"## Results\" section of the spec and names the file",
      target_spec.read_text(encoding="utf-8") == "# Spec\n\n## Expected results\n- E1 x\n\n## Results\n\n" + safe(report.report_text, with_equivalents) and "→" in printed, target_spec.read_text(encoding="utf-8"))
check("the Verification Report evidence line carries the spec the report was made for (path relative to the repository) under the key `spec` — the other lines do not",
      handed_specs == [("Verification Report", "spec.md"), ("Regression Test", None), ("Mutation Test", None), ("Requirements Traceability", None)], handed_specs)
SPEC_TEXT = "# Spec\n\n## Expected results\n- E1 x\n\n## Results\n"        # what main_with writes into the spec before the run
check("… and a short hash of the spec's expected results and finish line under the key `spec_hash` (the verdict compares it with the spec now) — the other lines do not",
      handed_hashes == [("Verification Report", safe(lambda: _evidence.spec_hash(SPEC_TEXT))), ("Regression Test", None), ("Mutation Test", None), ("Requirements Traceability", None)]
      and isinstance(handed_hashes[0][1], str) and handed_hashes[0][1] != safe(lambda: _evidence.spec_hash(SPEC_TEXT.replace("- E1 x\n", "- E1 x\n- E2 y\n"))), handed_hashes)
main_with(base(), spec_arg=str(target_spec.resolve()))
check("… the same relative path when --spec is given as an absolute path", handed_specs[:1] == [("Verification Report", "spec.md")], handed_specs)
check("spec_key: a spec inside the repository is its path from the root with forward slashes · one outside is the path as it is",
      safe(report.spec_key, target, target / ".oqa" / "specs" / "a.md") == ".oqa/specs/a.md" and safe(report.spec_key, target, target.parent / "elsewhere.md") == str(target.parent / "elsewhere.md"),
      safe(report.spec_key, target, target / ".oqa" / "specs" / "a.md"))
target_spec.write_text("# Spec\n\n## results\nnotes\n\n## Later\ntail\n", encoding="utf-8")
got = safe(report.write_into, target_spec, "### Verification Report\nX\n")
check("write_into: a `## results` heading (small r) is the results section — the report goes into it, no second `## Results` is appended (evidence.section reads headings in any letter case)",
      got == "# Spec\n\n## results\nnotes\n\n### Verification Report\nX\n\n## Later\ntail\n", got)
value, printed, handed, collect_args = main_with(with_equivalents)
check("without --yes collect gets the default limit of 30 minutes", [a[-1] for a in collect_args] == [30.0], collect_args)
value, printed, handed, collect_args = main_with(base(), "--yes")
check("without equivalent mutants no line carries a number of equivalents (None, not 0) · with --yes collect gets no limit",
      value == 0 and [entry[0] for entry in handed] == ["Verification Report", "Regression Test", "Mutation Test", "Requirements Traceability"] and [entry[-1] for entry in handed] == [None] * 4
      and [a[-1] for a in collect_args] == [None], (value, handed, collect_args))
NOTHING_TO_MUTATE = "no changed Python source to mutate (only test files, text files or deleted files changed) — no mutant was run"
check("when the change holds no Python source to mutate (a test-only change · a deleted source file) the Mutation Test line is recorded as `done` with that basis — rule changed: there used to be no line,"
      " so a finish line naming Mutation Test could never be met; it is not a `pass` (no mutant ran)",
      {entry[0]: entry[1:3] for entry in handed}.get("Mutation Test") == ("done", NOTHING_TO_MUTATE) and safe(report.check_results, base()).get("Mutation Test") == ("done", NOTHING_TO_MUTATE), handed)
zero_equivalents = base(mutation={"baseline": "pass", "generated": 1, "counts": {"killed": 1, "equivalent": 0}, "mutants": [row("killed")]})
value, printed, handed, collect_args = main_with(zero_equivalents)
check("a count of 0 equivalents is handed over as None", len(handed) == 4 and [entry[-1] for entry in handed] == [None] * 4, handed)
value, printed, handed, collect_args = main_with(dict(no_map, fingerprint=None))
check("a result without a fingerprint is recorded under \"unknown\" and the exit code is 2", value == 2 and len(handed) == 4 and {entry[3] for entry in handed} == {"unknown"}, (value, handed))
value, printed, handed, collect_args = main_with(with_equivalents, "--no-write")
check("with --no-write nothing is recorded and the spec is left as it was · the report is still printed",
      value == 0 and handed == [] and target_spec.read_text(encoding="utf-8") == "# Spec\n\n## Expected results\n- E1 x\n\n## Results\n" and "**Verdict: all passed**" in printed, (value, handed))



def main_exit(*extra):
    """(exit code of main, or "exit <code>" when the argument parser stopped it · what it wrote to the error stream · the --max value collect got)"""
    collect_args, errors = [], io.StringIO()
    old_collect = report.collect
    report.collect = lambda *a, **k: (collect_args.append(a), base())[1]
    try:
        with contextlib.redirect_stderr(errors), contextlib.redirect_stdout(io.StringIO()):
            try:
                value = report.main(["--repo", str(target), "--spec", "spec.md", "--no-write"] + list(extra))
            except SystemExit as stop:
                value = "exit %s" % stop.code
    finally:
        report.collect = old_collect
    return value, errors.getvalue(), [a[3] for a in collect_args]


got = main_exit("--max", "0")
check("report.py --max 0 is refused by the argument parser (exit 2, \"--max must be 1 or more\") — nothing is collected",
      got[0] == "exit 2" and "--max must be 1 or more" in got[1] and got[2] == [], got)
got = main_exit("--max", "-3")
check("report.py --max -3 is refused the same way", got[0] == "exit 2" and "--max must be 1 or more" in got[1] and got[2] == [], got)
got = main_exit("--max", "1")
check("report.py --max 1 is accepted and handed to collect as the number 1", got[0] == 0 and got[2] == [1], got)

# ── one real git repository without an impact map: collect and main end to end ──
print("a real repository without an impact map")


def git(root, *args):
    done = subprocess.run(["git", "-C", str(root), "-c", "user.email=t@t", "-c", "user.name=t"] + list(args), capture_output=True, text=True, encoding="utf-8", errors="replace", check=True)
    return done.stdout.strip()


real = Path(tempfile.mkdtemp()) / "real_repo"
(real / ".oqa" / "specs").mkdir(parents=True)
git(real, "init", "-q")
(real / "calc.py").write_text("def double(x):\n    return x * 2\n", encoding="utf-8")
(real / ".oqa" / "tests.json").write_text("[]", encoding="utf-8")
real_spec = real / ".oqa" / "specs" / "spec.md"
real_spec.write_text("# Spec\n\n## Expected results\n- E1 double is right\n\n## Results\n", encoding="utf-8")
git(real, "add", "-A")
git(real, "commit", "-qm", "first")
commit = git(real, "rev-parse", "HEAD")
got, printed = quiet(report.collect, real, real_spec)
got = got if isinstance(got, dict) else {"error": got}
check("real repository — HEAD of the result is the first seven characters of the commit", len(commit) >= 40 and got.get("HEAD") == commit[:7], (commit, got.get("HEAD"), got.get("error")))
check("real repository without a map — impact unknown \"no impact map …\" · mutation unknown \"no impact result …\"",
      got.get("impact") == {"unknown": "no impact map — run `impact.py record` first"} and got.get("mutation") == {"unknown": "no impact result, so the tests to select are unknown"}, got)
value, printed = quiet(report.main, ["--repo", str(real), "--spec", str(real_spec), "--no-write"])
check("real repository without a map — main ends with exit code 1 or 2 (never 0) and the printed report names the missing map on the impact and the mutation line",
      value in (1, 2) and "- Impact tests: unknown — no impact map — run `impact.py record` first" in printed
      and "- Mutation: no impact result, so the tests to select are unknown" in printed, (value, printed[-700:]))

print("\nfailures: %d" % len(failures))
sys.exit(1 if failures else 0)
