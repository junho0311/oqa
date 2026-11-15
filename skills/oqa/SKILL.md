---
name: oqa
description: Verify a code change with evidence instead of saying "done" from memory. Use when the user asks to verify, QA, check, or prove a change ("verify this", "is it really done?", "run QA", "mutation test this", "prove the tests catch it"), before claiming a coding task is complete or committing it, when starting work that should end in a verified result (write the spec first), or when looking at an unfamiliar Python repository for risky modules. OQA picks the checks the change needs, runs mutation testing on the changed lines, traces each requirement to a passing test, keeps evidence tied to a fingerprint of the code, and answers DONE only when every required check has evidence for the current code.
---

# OQA — evidence-based verification

OQA replaces "I think it's done" with a verdict computed from evidence.
Every verification result is stored with a **fingerprint of the code it looked at**; changing one line makes earlier evidence stale.
`core.py verdict` says `DONE` only when every required check has good evidence for the code as it is now and the user approved the spec.

`<oqa>` below is the plugin root — two folders above this skill's base directory (`skills/oqa/`); it is the folder that contains `scripts/`.
`<root>` is the root of the git repository being verified. The tools need Python 3.10+ and git, nothing else.

**Scope:** the automated checks (impact map, mutation, baseline) work on Python code with tests that run from a command line (plain scripts, `unittest`, `pytest`). For other languages only the reviews, the findings ledger and the evidence/verdict layer apply — say so instead of pretending a check ran.

## Rules

1. **Not run = not done.** Never report a check as passed unless its tool ran in this session and you saw the output. If a tool is missing or fails to start, say that.
2. **`core.py verdict` decides.** If it does not answer `DONE`, do not tell the user the work is complete; report the reasons it printed.
3. **The spec comes first and the user approves it.** Write the expected results before changing code. Only the user's own words, in double quotes, go under `## Approval` — never write an approval yourself.
4. **Do not lower the bar mid-way.** Do not shorten the finish line, cap the mutants, or mark a mutant equivalent to get to `DONE`. If a required check cannot be completed, stop and ask the user.
5. **A finding is not closed by saying it was fixed.** Accepting a finding needs a reproduction test that failed before the fix.
6. **Estimate before launching subagents.** Tell the user roughly how many tokens a review agent will read before starting it.

## One-time setup in a repository

1. `python <oqa>/scripts/init.py --repo <root>` — creates `.oqa/specs/`, writes `.oqa/tests.json` from the test files it finds (one entry per test file; for each it prints whether it runs with pytest, unittest or as a plain script and why — in a repository set up for pytest, unittest-style files run with pytest too), and adds the machine-local state files to `.gitignore`. Read its output: a file or folder it lists as "skipped", or "NOT written", means you write that entry by hand:
   ```json
   [{"name": "calc", "command": "python -m pytest tests/test_calc.py -q"}]
   ```
   Keep one entry per test file: mutation runs only the entries whose recorded lines include the mutated line, so one entry for the whole suite makes every mutant cost the whole suite.
2. Commit `.gitignore` together with `.oqa/tests.json` now, before the first change. A changed `.gitignore` is a file the tools cannot see into — it could hide a new code file from every tool — so until it is committed every report is "cannot judge". Specs (`.oqa/specs/`), `.oqa/current` and the findings ledger (`.oqa/findings.jsonl`, created by the first finding) also belong in version control — commit them when they exist. The state files (`evidence.jsonl`, `impact-map.json`, `core.json`, `baseline.json`) stay local. Nothing under `.oqa/` ever counts as a change to verify, so it does not matter whether a spec is committed before or after the code.
3. A repository you have not seen before: `python <oqa>/scripts/baseline.py --repo <root>` prints a risk ranking (coverage and a mutation sample per file). It records the impact map itself when there is none.

## The loop for one change

1. **Spec.** Copy `templates/spec.md` (next to this file) to `<root>/.oqa/specs/<date>_<name>.md` and write just that file name (for example `2026-01-15_min-length.md`, no folder) into `<root>/.oqa/current`. If `.oqa/current` names a file that is not a spec in `.oqa/specs` (a missing file, a typo), the tools never switch to another spec on their own: the plan prints a `spec problem:` line and the verdict is `INCOMPLETE` with that as its first reason — correct the file name, then run plan again. Without `.oqa/current` the spec is known only when `.oqa/specs` holds exactly one spec file; with several, plan and verdict say the same way that you must name one. Fill in what and why, the numbered expected results (`- E1 …`), and the finish line (the checks this work must satisfy — only check names on that list line: any other text there is read as an unknown check name and the verdict asks about it). The other sections (Scope, Tools, Independent review gate) are optional; the `<-` hints in the template are notes for you and may be deleted. Show it to the user and wait for approval; put their words under `## Approval`.
2. **Map, on a clean commit, before editing:** `python <oqa>/scripts/impact.py record --repo <root>` (skip it when the map was recorded on this same commit, for example by `baseline.py` a moment ago).
3. **Change the code and write tests.** Tag the test that proves each expected result with a comment `# verifies: <spec file name without .md>/E1`. The tag marks the whole test file (one `tests.json` entry): the item passes when that file passes, so keep the test of each expected result honest yourself — the tool does not check that the tagged test really exercises it. A **new test file** needs an entry in `.oqa/tests.json` (write it by hand, or run `init.py --repo <root> --force`, which writes the whole list again from the test files found): only listed tests are run. Editing `.oqa/tests.json` does not make the map stale, and the map is not recorded again for it.
4. **Plan the checks:** `python <oqa>/scripts/core.py plan --repo <root>` — prints which checks this change needs and why (required / recommended). A line `test file(s) not listed in .oqa/tests.json: …` names changed or new test files without an entry — add the entries before the report.
5. **Manual checks first** (reviews), if any were picked. For each: measure the fingerprint, launch the agent, judge its findings, record the evidence — see "Manual checks" in `references/verify.md`. The agents shipped with this plugin: `silent-review`, `reasoning-review`, `reasoning-refute`, `blind-check`. Record findings with `findings.py add`; fix them together. At most two review rounds — if a third seems needed, ask the user.
6. **Report, once, before committing:** `python <oqa>/scripts/report.py --repo <root> --spec .oqa/specs/<spec>.md`
   Use the spec that `.oqa/current` names — the verdict refuses a report made for another spec, and asks for a new report when the spec's expected results or finish line were changed after this one. It runs the affected tests, mutates every changed line, checks each expected result has a passing test, writes a `### Verification Report` into the spec, and records the evidence. Every mutant of the changed lines runs (no `--max` needed), in parallel copies (`--jobs auto` by default: half the CPUs, at most 8).
   Before the mutants start it prints a time estimate. If even the low estimate is over 30 minutes (`--stop-over`), it stops and the report is "cannot judge": tell the user the estimate and ask whether to go ahead (`--yes`) or to make the tests faster first — do not pass a small `--max` to get under the limit (that runs only a sample).
7. **Verdict:** `python <oqa>/scripts/core.py verdict --repo <root>`
   `DONE` → commit (the report belongs in the same commit). The verdict is about the uncommitted change. Run plan, report and verdict **before** committing: once the change is committed there is nothing left to check — the plan picks no checks and the verdict answers `NEEDS DECISION — there is no uncommitted change to verify`, never `DONE`. `INCOMPLETE` → fix only the listed reasons, then repeat from step 4 (any fix makes all evidence stale, so collect fixes and redo once). `NEEDS DECISION` → something the tool cannot settle by itself is listed. Where the message names the fix (a new test file without an entry in `.oqa/tests.json`, a mutation run stopped on its time estimate), do that and repeat from step 4. For the rest (a file it cannot see, an equivalent mutant, an unclear gate line, nothing left to verify) there is no command that turns this into `DONE`: tell the user exactly what is listed and what you did about it by other means, and let them decide whether to accept the change. `UNKNOWN` → something could not be measured; say what.

## Reading the results

| You see | Meaning | What to do |
|---|---|---|
| mutant `survived` | a changed line can be broken without any test failing. The line shows file, line, the kind of change (`number +1`, `compare Gt → GtE`, …) and the line as it was mutated; `drafting.py bundle` writes a request page for a missing test per survivor | add a test that pins that behaviour, or — only if no test could ever tell the difference — declare it under `## Results` as `- equivalent mutant: <file>:<line> <kind> — <reason>`; Core then asks the user to confirm |
| `gap` | changed lines no recorded test passes through. A `gap … added line(s)` line printed early in a run is provisional — lines added after the map was recorded are settled later in the same run; the report's `Gaps:` line is what counts | if the report still lists a gap, add a test and run the report again (the map is not re-recorded for this): the gap is lifted when mutants ran on those lines and every one was killed. A changed line that gives no mutant (a comment) cannot be lifted this way. A gap of a new file under a tests folder that is not named like a test (`tests/helpers.py`) says in its text what is missing |
| `Added lines not confirmed — mutants on them survived` (or `Changed lines not confirmed — …`) | tests do run through those added or changed lines, but a mutant on them got past every test. This is not a coverage gap | fix the survivors listed under `Mutation` (see `survived` above); the line goes away with them |
| `Added lines with nothing to mutate` | added lines that hold nothing a mutant could change (imports, comments, blank lines, docstrings — and the few statements no operator probes: `pass`, `global`, `assert`, `break`, a `print(...)` line, the header line of a `def`, `class`, `try`, `with` or `for`). Not a gap | nothing — informational |
| `<file> looks like a test file that is not listed in .oqa/tests.json` | a new test file (`test_*.py`, `*_test.py`) has no entry in the test list, so it was never run; the report is "cannot judge" on that account. `core.py plan` prints the same files in one line | add an entry for it to `.oqa/tests.json`, or run `init.py --repo <root> --force`, then run the report again. A listed new test file is run and confirmed by its own passing run |
| check state `stale` | the evidence is for older code | run that check again |
| check state `missing` | never run for this code | run it, or ask the user to take it off the finish line |
| `Verdict: incomplete <n>` | n items are listed right under it (survived mutants, gaps, failed tests, expected results without a passing test) | fix those |
| `[hunk level 2]`, `(results given 1)` | how the tests for a mutant were chosen (by line, by changed hunk, by file, by name) and how many tests it had; on the `baseline passed` line, how many of the tests had a result handed over from earlier in the same run (each is still run once, unmutated, in the copy) | nothing — informational |
| `cannot judge` | something could not be measured: no test selected, a test that fails before any mutant (in the repository or in the copy the mutants run in), only part of the mutants run, a changed file the tool cannot see (anything that is not Python or plain text — a changed `.gitignore`, `requirements*.txt` and `constraints*.txt` are not plain text), a changed source file with no mutant, a new test file that is not listed | read the listed reason and fix the cause; do not treat it as a pass. A changed non-Python file is yours to verify by other means — say so to the user |

## Reference

- `references/verify.md` — every check in detail, the evidence procedure for manual checks, the verification report, manual mutation lists, drafting missing tests, the independent review gate, running tests by case
- `references/review-scope.md` — what to review and how deep, shared by all reviews
- `python <oqa>/scripts/core.py rules` — the exact rule table
- `python <oqa>/scripts/evidence.py show --repo <root>` — state of every check for the current code
- `python <oqa>/scripts/findings.py show --repo <root>` — open findings
