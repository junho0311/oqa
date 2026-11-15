# Verification

How OQA's checks work, what counts as evidence, and in which order to run them.
Decide **before starting** what you will look at to call the work done — not after the implementation is finished.

In the commands below `<oqa>` is the root folder of this plugin and `<root>` is the root of the repository being verified.
All tools use the Python standard library only (no coverage package, no test framework is installed for them).

---

## Definition of done

**If it was not run, it is not done.** Write "done" only after running it — and with OQA, only when `core.py verdict` answers `DONE`.

- A test that passes without an assertion is fake. A test that does not say what must equal what always passes
- Before starting, write the **success condition as a sentence** (the spec's `## Expected results`, numbered `E1`, `E2`, ...). If you cannot write it, it is not a goal yet
- For work with several steps, write how each step is confirmed

## Fake operation — the worst failure

Talking as if a tool that is not installed had been called, and making up its result, is something that really happens.
A verification tool that runs fake is worse than no verification at all: without it the item stays "not confirmed", but with a fake run **it is recorded as confirmed**.

| Situation | What to do |
|---|---|
| Before calling a tool | Check first that it exists |
| When it does not exist | **Say that it does not exist.** Do not pretend and invent a result |
| When falling back to a substitute procedure | State that a substitute was used |
| When only part was run | Write how far it was run. Do not write "mostly passes" |

---

## Core — the tool decides what to verify and whether it is finished

Which checks to run, and whether the bundled results mean "done", must not be left to the memory of the conversation.

| When | What to do |
|---|---|
| After the change, entering verification | `python <oqa>/scripts/core.py plan --repo <root>` — looks at the kind and size of the change and at the spec, prints **the checks to run and the reason for each** as a table, and writes `.oqa/core.json`. Run those checks. A picked check is left out only through the spec's `## Finish line` (see below), which the user approves — a note in the spec does not take a required check off the verdict. The plan also names, in one line, changed or new test files that have no entry in `.oqa/tests.json` |
| Before saying "it is finished" | `python <oqa>/scripts/core.py verdict --repo <root>` — for every check, is there **evidence with the same fingerprint as the code now** and is its result good, plus the user's approval: `DONE` (exit code 0), `INCOMPLETE` (1), `UNKNOWN` (2), `NEEDS DECISION` (3). **If it is not DONE, do not say the work is complete** |
| The rule table itself | `python <oqa>/scripts/core.py rules` |
| A repository seen for the first time, or "something is wrong somewhere" | `baseline.py` (baseline and risk map, see below), then a reasoning review of the modules at the top |

### The checks

| Check | Picked when | How it works | Who records the evidence |
|---|---|---|---|
| Regression Test | anything changed | only the tests that pass through the changed lines are selected from the impact map and run; the listed tests of a brand-new test file are run as well | `report.py` |
| Mutation Test | source `.py` changed (test files excluded) | the changed lines are broken on purpose in a copy and the selected tests must fail. When the change holds no Python source to mutate (for example a test-only change) it is recorded as `done` — there was nothing to mutate, so a finish line that names the check can still be met; it is not the `pass` of a run | `report.py` |
| Requirements Traceability | code, UI or config changed and the spec has numbered E items | every E number must have a test carrying `# verifies: <spec name>/E<n>` and that test must pass | `report.py` |
| Manual Mutation Test | regex or string splitting on added lines, or a large code change | hand-written text replacements the mutation tool cannot generate | `manual_mutate.py` |
| Silent Failure Review | a changed code file whose name suggests it judges or counts data, or a large code change | the `silent-review` agent counts and compares | the main conversation (manual check) |
| Reasoning Review | structural change (several new `.py`, or many changed lines) | `reasoning-review`, then `reasoning-refute`, then reproduction tests | the main conversation (manual check) |
| Blind Review | the spec's `## Tools` section names `blind-check` or "blind review" | the `blind-check` agent judges again without the conclusion | the main conversation (manual check) |
| Fuzz Test (LLM) | several regex, parse or split calls on the added lines of one source file | bulk input plus invariants, with inputs written by a small model (see below) | the main conversation (manual check) |
| E2E UI Test | UI files (`.html`, `.css`, `.js`) changed | drive the real screen with whatever browser tool the project has; OQA ships no runner | the main conversation (manual check) |
| Independent Review Gate | the spec's independent review gate is on | a fresh reviewer that gets only code, spec and tests (see below) | the main conversation, with the fingerprint measured when the bundle was made |
| Baseline | there is no impact map | `baseline.py` | `baseline.py` |

The exact conditions and thresholds are the output of `core.py rules`.

### Evidence — tools record it, Core only matches fingerprints

Core does not guess from run timestamps, command text or patterns in the spec text; every such guess opens a path to a wrong `DONE`.
For each verification a line "check, **fingerprint of the code at that time**, result" is appended to `<root>/.oqa/evidence.jsonl`,
and Core counts only the lines whose fingerprint equals the fingerprint now.

- The fingerprint is the git tree hash of the working tree: untracked files that `.gitignore` ignores are left out, files git tracks are always in (also when `.gitignore` names them), and `.oqa/` is left out. Measuring it has a 60-second limit; for a very large repository raise it with the environment variable `OQA_FINGERPRINT_TIMEOUT` (seconds) — a fingerprint that cannot be measured makes the verdict `UNKNOWN`, never `DONE`. Changing one line of code makes **all** earlier evidence (tests, reviews, report, gate approval) stale at once
- Results: `pass`, `approved`, `done` are good; `fail`, `rejected` are bad; `unknown` is neither and is never folded into a pass
- Check states: `ok`, `failed`, `unknown`, `stale` (evidence for different code), `missing`
- From the spec text Core reads only **the user's approval** (`## Approval`, the user's words in double quotes) and **the quoted words that switch the independent review gate on**. The Verification Report written into the spec is a copy for people — writing "all passed" or an approval there does not change the verdict
- The verdict also compares a hash of the spec's `## Expected results` and `## Finish line` with the one stored when the report was made: changing either after the report needs a new report (`report.py` again). A report made for another spec than the current one is refused
- The state now: `python <oqa>/scripts/evidence.py show --repo <root>`

### Finish line

Put a `## Finish line` section in the spec and list the check names this piece of work must satisfy, for example
`- Regression Test · Mutation Test · Requirements Traceability`. `core.py plan` then splits the picked checks into **required** (on the finish line)
and **recommended** (outside it), and the verdict counts only missing or stale *required* checks as incomplete.
A check that was run and **failed** is incomplete even when it is only recommended. Without the section every picked check is required.
The finish line is part of the spec the user approves — do not shorten it in the middle of verification
(otherwise the verification needed is decided by threshold guesses, and every fix picks another review and the work never ends).
A name in the finish line that is not in the rule table is raised by the verdict as something to confirm, so a typo does not silently drop a check.

### Findings ledger

Findings from reviews (subagents, an independent reviewer, the user) go into `.oqa/findings.jsonl`:

```
python <oqa>/scripts/findings.py add --repo <root> --who silent-review --severity high --what "<what is wrong>" --where <file:line> --check "Silent Failure Review"
python <oqa>/scripts/findings.py close --repo <root> --id F-3 --status accepted --repro-test <name of the reproduction test>
python <oqa>/scripts/findings.py close --repo <root> --id F-4 --status rejected --reason "<why>"
python <oqa>/scripts/findings.py show --repo <root>
```

- Severity is `high`, `medium` or `low`. Closing statuses are `accepted` (needs `--repro-test` — saying it was fixed is not enough), `rejected` and `deferred` (both need `--reason`)
- An open **high** finding keeps the verdict `INCOMPLETE`
- A finding recorded with `--check "<check name>"` keeps the verdict `INCOMPLETE` until it is closed, whatever its severity — a review is not finished just by being run
- Each finding keeps the code fingerprint of the moment it was recorded and of the moment it was closed

---

## Order of work — at most two review rounds, the report once

1. **Before changing anything**, on a clean commit: `python <oqa>/scripts/impact.py record --repo <root>`. The map is based on HEAD — a map recorded while files have uncommitted changes is stale. If a test fails while the map is recorded, `record` says which and exits 1: fix that test first, a map taken from a failing run is not a baseline. (A new code file has no lines in the map; the report generates mutants over the whole file and treats it as confirmed when all of them are killed. A new test file is not in the map either: give it an entry in `.oqa/tests.json` — by hand or with `init.py --force` — and the report runs it and confirms it by its own passing run. Editing `.oqa/tests.json` does not make the map stale)
2. Make the change, then `core.py plan`. Of the picked checks run the **manual checks first** (the reviews), collect their findings and fix them all together (reproduction tests, manual mutation)
3. If Core picks a manual check again for the fixed code, run it **once more only** (the second round). If a high finding remains after two rounds, or Core picks yet another, do not run a third — **ask the user**. Without a stopping rule the rounds do not end
4. When there is nothing left to fix: `core.py plan` again, then `python <oqa>/scripts/report.py --repo <root> --spec .oqa/specs/<spec>.md` **once** (when the independent review gate is on, run the gate on the same code), then `core.py verdict`
5. If the verdict is `DONE`, commit. If it is `INCOMPLETE`, fix only the reasons given and go back to 4. A fix changes the fingerprint and turns all evidence stale — that is why fixes are collected and made in one go

Run `report.py` **before the commit**. After the commit the working change is empty, so nothing is selected and the result is "cannot judge"; the report goes into the same commit as the change.

---

## Manual checks — the evidence procedure

A review looks at the code **as it was when the reviewer started**, so its evidence must carry that fingerprint, not the fingerprint at the time of recording.

1. Right **before** launching the reviewer (or running the UI or fuzz check): `python <oqa>/scripts/evidence.py fingerprint --repo <root>` — keep the value
2. Launch the agent. Read its findings and **judge each one** (record them in the findings ledger; accept, reject or defer)
3. After judging: `python <oqa>/scripts/evidence.py record --repo <root> --kind "Silent Failure Review" --result done --fingerprint <the value kept in 1> --basis "<findings n, accepted n>"`

`evidence.py record` takes only the manual checks (`Silent Failure Review`, `Reasoning Review`, `Blind Review`, `Fuzz Test (LLM)`, `E2E UI Test`) and `Independent Review Gate`,
and always with `--fingerprint`, a value that was measured: recording with the fingerprint now would claim the review also looked at code changed afterwards, and a value of `unknown` is refused.
The kinds that tools record (`Regression Test`, `Mutation Test`, `Requirements Traceability`, `Verification Report`, `Manual Mutation Test`, `Baseline`) are refused on the command line — run the tool that records them.
A mistyped `--kind` is refused instead of leaving the real check silently missing.
If the code was fixed after the review started, the evidence is stale for the new code — which is correct; that is the second round.

---

## The Verification Report — "done" as a table counted by the machine

The test list is `<root>/.oqa/tests.json`: `[{"name": "...", "command": "..."}]`, with an optional `"fail_pattern"` per test. `python <oqa>/scripts/init.py --repo <root>` writes it from the test files it finds, one entry per file.
A test counts as failed when it ends with an exit code other than 0, runs into the time limit, or its output matches the failure pattern (by default lines such as `FAIL:`, `ERROR:`, `FAILED ...`, `failures: 3`, or a summary line with a non-zero number of failed tests).
Only listed tests are run: a new test file needs an entry before the report (`init.py --force` writes the whole list again from the test files found).

| Tool | What it does | What it counts |
|---|---|---|
| `mutate.py` | breaks only the changed lines through the syntax tree and runs the tests in a copy of the repository | **killed** (a failure line in the test output, or the run ended with an error and exit code ≠ 0 — the original was first seen to pass in the same copy, so the mutant is to blame), **survived** (no failure line and exit code 0), **crashed** (timeout — not killed: it is unknown whether a test bit that rule). When `--max` is smaller than the number of mutants, an evenly spread sample runs. A line that is only a `print` is not removed |
| `impact.py record` / `select` | measures for each test the lines it passes through (subprocesses included) and selects only the tests that pass through the changed lines | **gap** (no test passed through the changed lines), **unseen** (non-Python files), **stale** (HEAD differs, recorded with modified files, or the range does not line up with the map). Files under `.oqa/` are neither "changed" nor "unseen", and a deleted `.py` file is not a gap (the tests that passed through it are selected, and that is all there is to check) |
| `traceability.py` | matches the E numbers of the spec's `## Expected results` with the tests carrying `# verifies: <spec name>/E<n>` | `no marker`, `outside test list`, `pass`, `fail`, `not run`, `unknown`, `no number`, `duplicate number` (never flattened to 0) |
| `report.py` | bundles the three and writes `### Verification Report` under the spec's `## Results`, fingerprint included | `all passed`, `incomplete`, `cannot judge` |

```
python <oqa>/scripts/impact.py record --repo <root>
python <oqa>/scripts/impact.py select --repo <root> --run
python <oqa>/scripts/mutate.py --repo <root> --files src/a.py --test "python tests/a_test.py" --json mutants.json
python <oqa>/scripts/traceability.py --repo <root> --spec .oqa/specs/<spec>.md --run
python <oqa>/scripts/report.py --repo <root> --spec .oqa/specs/<spec>.md
```

When it writes the report, `report.py` records one evidence line per check (`Verification Report`, `Regression Test`, `Mutation Test`, `Requirements Traceability`)
**with the fingerprint measured at that moment**. With `--no-write` neither the report nor the evidence is written.
If the code changes while the tests are running, the result is "unknown" — it is not known which code it belongs to.
The `Mutation Test` line is `done` when the change has no Python source to mutate (only test files, text files or deleted files changed — for example a test-only change): no mutant was run, and a finish line that names the check is still met.
The `Verification Report` line also carries the spec the report was made for and a hash of that spec's expected results and finish line, which the verdict compares with the spec now.

### How to read it

- The header line gives who counted (`report.py`), when, the HEAD, the range and the fingerprint
- `**Verdict: all passed**`, `**Verdict: incomplete N**` (followed by the N entries) or `**Verdict: cannot judge**` (followed by `- unknown: ...` lines)
- The table `Expected result | Verifying tests | Verdict` — one row per E item. `· not run: <file>` after the tests names a file that carries the item's tag but is run by no test of `.oqa/tests.json`: the item is then `not run` (cannot judge) even when the other tests passed — list that file, or remove the tag
- `Impact tests` — changed files, selected tests out of the total, how many passed, and `map stale` when the map must be recorded again
- `Gaps` — changed lines no test passed through
- `Test files not listed in .oqa/tests.json (not run)` — new test files without an entry in the test list; each also has an `- unknown: <file> looks like a test file that is not listed in .oqa/tests.json — add an entry …` line under the verdict
- `Added lines not confirmed — mutants on them survived` (and `Changed lines not confirmed — mutants on them survived`) — added lines, or changed lines no recorded test passed through, whose mutants got past the tests: fix the survivors listed under `Mutation`; this is not a coverage gap, and the file is not listed under `Gaps` as well
- `Added lines · new files — confirmed by other evidence` and `Changed lines no recorded test passed through — confirmed by other evidence` — gaps that were lifted (see below)
- `Added lines with nothing to mutate` — added lines that hold nothing a mutant could change (imports, comments, blank lines, docstrings — and the few statements no operator probes: `pass`, `global`, `assert`, `break`, a `print(...)` line, the header line of a `def`, `class`, `try`, `with` or `for`); not a gap. A new file that holds only such lines (an empty `__init__.py`, a module of bare definitions) is confirmed the same way
- `Unseen (non-Python)` — files the machine cannot see, and how many of them are code
- `Mutation (changed lines)` — generated, run, killed, survived, crashed, gap, equivalent, followed by one line per survived, crashed, gap or equivalent mutant (a survived line also shows the line as it was mutated)
- `Test runs` — how many times tests were started and the elapsed time

Rules that keep the verdict from being reduced:

- **Unknown is not pass.** A timeout (crashed), a stale map, expected results with no E number, and no test selected for the change are "unknown" and make the verdict "cannot judge"
- **A changed file the tools cannot see is unknown, whatever its type.** Only plain text counts as text: `.md .txt .rst .adoc`, images and PDFs (`.png .jpg .jpeg .gif .svg .ico .pdf`), and files named `LICENSE`, `NOTICE`, `CHANGELOG`, `AUTHORS`, `.gitattributes`, `.editorconfig`. `requirements*.txt` and `constraints*.txt` are not plain text (they decide which code runs), and neither is `.gitignore` (a changed `.gitignore` can hide a new code file from every tool — commit it before the change). Every other changed non-Python file (`.js`, `.go`, `.toml`, a `Dockerfile`, …) is code the tools did not look at — verify it by other means and tell the user so. Files under `.oqa/` are never counted, neither as changed nor as unseen
- **The tests must pass in the copy before any mutant counts.** Mutants run in throw-away copies that leave out `.git`, `.oqa`, `node_modules` and `__pycache__`. Every test a mutant will use is first run once, unmutated, in the copy; a test that passes in the repository but fails there stops the run ("passes in the repository but fails in the copy") instead of making every mutant look killed. Make such a test independent of those folders, or it cannot be used for mutation
- Changed code with **0 generated mutants** (deleted lines, `def` headers only) is "cannot judge", not a pass — it is unknown whether the tests bite that change. 0 does not mean "no weak tests". A change with no Python source to mutate at all (only test files, text files or deleted files) is another case: the `Mutation Test` evidence is then `done`
- New files and large additions have no lines in the map and appear as gaps. An "added lines" gap is not counted as incomplete when other evidence exists: for a code file, at least one mutant was run **inside that added range** and all were killed (mutants on other changed lines of the same file do not count), or the added lines hold nothing to mutate (the list above: imports, comments, blank lines, docstrings and the statements no operator probes) and every generated mutant was run; for a test file, all selected tests passing through it passed. A file whose change only adds such lines counts as "nothing to mutate" as a whole, not as "0 generated mutants"
- When a mutant inside the added lines **survived**, the file is labelled "added lines not confirmed — mutants on them survived" and the survivors are the one thing listed as incomplete — not a second "gap" entry
- A new code file is an "added lines" range as a whole — when every mutant generated in that file is killed, it is confirmed
- Changed lines of an existing code file that no recorded test passed through (the test for them was added with the change, after the map was recorded) are confirmed by the same evidence: mutants ran on those lines, none was left out by `--max`, and every one was killed — a kill is proof that a test executes the line and checks it. With zero mutants on them (a comment-only change), a survivor, a crashed mutant or a sample, the gap is not lifted
- A new file under a tests folder that is **not named like a test** (`tests/helpers.py`) is support code for the tests: it is mutated like a new code file and confirmed when every mutant generated in it is killed; otherwise its gap says what is missing (if it is a test, rename it and list it)
- A new **test file** (`test_*.py`, `*_test.py` — the names `init.py` lists) is confirmed by its own run: `report.py` runs the entries of `.oqa/tests.json` whose command names the file, and when they pass the file is not a gap; those tests are also used for the mutants of the change (a test that is in the list but not in the map runs for every mutant). Without an entry the file was never run: the report says that it looks like a test file that is not listed and to add an entry (or run `init.py --force`), and is "cannot judge" on that account
- A deleted `.py` file is not a gap. A renamed file is a deleted file and a new one (git's rename detection is switched off, as in the plan)
- A test the map knows but `.oqa/tests.json` no longer lists (removed or renamed in this change) is left out and said; a listed test the map does not know (added or renamed after the map was recorded, a new case) is run by `report.py` and its result counts
- The report ends with an end line (an HTML comment). The next run replaces the old report up to that line; what you write below it inside `## Results` — an `- equivalent mutant: …` line, review notes — is kept
- When mutants were cut down by `--max`, the result is "cannot judge" — a sample says nothing about the lines that were not tried. `report.py` runs **all** of them by default; pass `--max` only when you deliberately want a quick look, never to reach a verdict
- A file that cannot be mutated at all (not valid UTF-8, a syntax error) is reported as such and is "cannot judge"
- **Run all mutants.** Do not lower the rule with a sample or a time cap. Speed-ups are only those that do not cut the effect: a killed mutant stops there, a test process ends when a failure line appears, fast tests first, several copies (`--jobs`, default `auto`: half the CPUs, at most 8), tests split into cases. Before the mutants start a time estimate is printed (a low and a high figure). It counts measured test time only — copying the repository and starting the processes come on top — and it can always be made: a test without a measured time gets the time of its unmutated run in the copy. When even the low estimate is over `--stop-over` minutes (default 30) `report.py` stops with "cannot judge" so the user can decide — go ahead with `--yes`, or make the tests faster first
- Crashed mutants are shown line by line with the reason — read them and decide whether a test really bit

### Tests split into cases

A large test file can be split into cases with `# case: name` lines, as long as the test command answers `--list-cases` (the line `# case list`, then one name per line) and `--case a,b`.
List it in `.oqa/tests.json` with `"cases": true`: the impact map and the mutant selection then work per case, and a mutant runs only the cases that pass through its line.
A test that uses a shared outside resource (a port, a real browser) gets `"exclusive": true` — only one of them runs at a time even with `--jobs`,
so a failure caused by colliding runs is not counted as "killed".

### What these tools cannot see

The meaning inside string and regex literals, non-Python code, tests that copy the code out of the repository to run it. A `verifies:` marker tags a whole test file (a `tests.json` entry), not a single test function inside it.
For the first of these there is manual mutation.

---

## Equivalent mutants

A mutant whose behaviour is identical after the change cannot be caught by any test. Declare it in the spec's **`## Results` section**:

```
- equivalent mutant: <file>:<line> <kind> — <reason>
```

- File, line and kind must equal those of the "survived" line in the report, and an entry without a reason is ignored
- If two or more survived mutants share that line and kind, none is excluded; a declared line that matches nothing is shown in the report
- The report shows the excluded lines separately as "equivalent", and **Core raises `NEEDS DECISION` whenever there is one** so that the user looks at the reason — no self-granted exemption
- **First see whether a test can catch it.** If a test that compares the function's return value directly kills the mutant, it was not equivalent

---

## Manual Mutation Test

For the kinds the tool cannot generate (a character in a regex, a wording, one word of a condition):

```
python <oqa>/scripts/manual_mutate.py --repo <root> --list <list.json>
```

The list is `[{"name": ..., "file": ..., "pairs": [[old, new]], "test": ...}]` (`test` is a test name from `.oqa/tests.json` — a case is `test:case` — or a command text). Each old text must appear exactly once in the file;
0 times or several times means it is unknown which spot was broken.
The tool works in a copy, checks first that the test passes there before anything is broken (with a test that already fails, whatever is broken looks "killed"), and removes the copy when it ends.
It records the evidence itself: all killed → `pass`, any survived or crashed → `fail`, baseline failed or not replaced → `unknown`.
The evidence name is the list file name — the latest result of each list counts, and one list's failure is not covered by another list's pass.
When the code changed while it ran, or the fingerprint could not be measured, it records nothing and says so in one line (`evidence not recorded — …`): run it again on code that stays as it is.

---

## Survived mutants → drafts of the missing tests

1. When `mutate.py ... --json mutants.json` shows survived mutants: `python <oqa>/scripts/drafting.py bundle --repo <root> --result mutants.json --out <scratch folder outside the repository>` — one request page (`n.md`) and one mutant description (`n.json`) per survived mutant
2. Hand each request page to a small-model subagent and receive `n_candidate.py` — let it write only into the folder outside the repository, and tell it to read only the target file
3. `python <oqa>/scripts/drafting.py check --repo <root> --bundle <folder>/n.json --candidate <folder>/n_candidate.py` — accepted only when the draft **passes on the original code and shows a failure line on that mutant**, both. Whether to accept is decided by the machine, however plausible the draft looks
4. Read the accepted draft yourself and move it into the test folder — then run the same mutant again with the new test and see that it is killed

If the file changed after the mutants were generated, the bundle is stale and the tool says so — run `mutate.py` again.

---

## Reasoning review — independent, refuted, reproduced

A reviewer of the same model family that has heard the author's explanation misses the same places the author missed.
**When**: structural changes, judging logic, the risky modules of a system seen for the first time. Not for small fixes — it is expensive.

| Step | Who | What, and when it ends |
|---|---|---|
| 1 Bundle | main conversation | In a scratch folder: `target/` (changed code and diff, or a copy of the module), `requirements/` (the spec's what, what to change, the E items, source documents), `tests/`, `0_request.md`. **No author explanation, no conclusion, no conversation, no "it works"** — confirm by counting the file list |
| 2 Review | `reasoning-review` | Four lenses (purpose ↔ implementation, interaction, scenario and counterexample, gaps in the requirements). Every finding has its kind of basis, reasoning trail, confidence and a way to reproduce it |
| 3 Refute | `reasoning-refute` | Each high or medium finding gets one of three answers: refuted, not refuted, or partly refuted. **Zero high or medium findings pass without a refutation attempt.** Give the findings to one agent as a batch rather than one agent per finding (most of the cost of an agent is its base context) |
| 4 Judge | main conversation | For a code-defect finding that was not refuted, **write the reproduction test first** and see it fail before the fix, then accept it. If it cannot be reproduced, keep it with its basis as "reasoning, unconfirmed" (neither accepted nor dropped). A refuted finding is rejected with the reason |
| 5 Record | main conversation | In the spec's `## Results`: number of findings, refuted, not refuted, reproduced, unconfirmed. Findings go into the findings ledger with `--check "Reasoning Review"`; the evidence line follows the manual-check procedure above |

## Blind review

For a high-risk judgement (money, permissions, the interpretation of a rule, whether a requirement is met), before it goes out to the user.
Make a copy folder **outside the repository** with `0_question.md` (the question and scope — no conclusion), `target/` and `criteria/`, and give the `blind-check` agent only that path.
Afterwards compare the "Files read" of its answer with the paths of its tool calls: the agent's tools are not restricted to the folder, so staying inside it is a promise that has to be checked.
Core picks this check only when the spec's `## Tools` section names it.

---

## Independent review gate

The gate is for work where the user wants a second, independent reading of the finished result. It is on only when the user said so: the spec has

```
## Independent review gate
Independent review gate: on (<date> "<the user's words>")
```

(the last such line wins; `off` means off; `on` without quoted user words, a placeholder left as `<…>`, or other text in the section is unclear and the verdict asks about it).

One round:

1. Measure the fingerprint (`evidence.py fingerprint --repo <root>`) and make a bundle of **code, spec and tests only** — no author explanation, no conversation, no conclusion
2. Start a **fresh subagent** that has seen nothing of this conversation and give it the bundle (the `reasoning-review` agent can serve). It returns findings and an overall answer: approve or reject
3. Give every high or medium finding to `reasoning-refute`
4. For each finding that survives, write a **reproduction test** and see it fail before fixing. Record the findings in the findings ledger with `--check "Independent Review Gate"`
5. Record the round with the fingerprint from step 1: `python <oqa>/scripts/evidence.py record --repo <root> --kind "Independent Review Gate" --result approved --fingerprint <value> --basis "<round n, findings n>"` (or `--result rejected`). The approval belongs to the code that was in the bundle — after a fix the fingerprint differs and a new round is needed

After three rounds without an approval for the code now, Core answers `NEEDS DECISION`: ask the user whether to run it once more.

**How independent this is.** A subagent of the same model family as the author is a weaker independent eye than a model from a different vendor:
it shares training and blind spots with the author, so it tends to miss the same things. Withholding the author's explanation removes one source of
agreement, not that one. If a reviewer from another vendor (or a person) is available, give it the same bundle and record its result the same way;
if not, say in the report that the gate was run by a same-family model.

---

## Baseline — a system seen for the first time

Impact tests, mutation, traceability and the report only work **when there is a change**. For a repository seen for the first time (or a task of the kind "something is wrong somewhere"), take a baseline first:

```
python <oqa>/scripts/baseline.py --repo <root> --sample 6
```

It records the map (when missing or stale), then for every file and function the number of tests passing through, the share of covered lines and the functions no test passes through,
then for every file the killed share of a sample of mutants (`--sample k` per file; `--no-mutation` skips this step), and from these a
**risk map** (low coverage × low killed share × large size at the top). A cell that was never measured reads "not measured". The result is `.oqa/baseline.json`.
When a test failed while the map was recorded, the baseline is still written but the tool exits 1 and its `Baseline` evidence line is `fail` — the coverage comes from a failing run; fix the test and take the baseline again.
Start reasoning reviews from the modules at the top, **module by module** (given everything at once the review turns shallow).
Take the list of modules from the baseline ranking, the review targets from the shared table in [review-scope.md](review-scope.md), and the four lenses from the `reasoning-review` agent definition.

---

## Try to break it — before closing with "correct"

Before closing a requirement or a judgement as "satisfied" or "correct", make **several different attempts to break it**. One attempt that did not break it only means that one path holds.

- Choose a **different axis** for each attempt: boundary values, empty values, pressing twice (concurrency), other input shapes (encoding, dates, numbers with separators), exceptions, failure in the middle, permissions, bypass paths
- Make the attempts **in a working copy**, and keep for each what was put in and what came out — "tried it" alone makes the next person do the same again
- If it breaks, that is a finding (keep it as a reproduction test). If everything holds, write "did not break under these attempts" — not "correct"
- Whether the axes differ matters more than the number of attempts

## Fuzz Test (LLM) — bulk input, invariants, and a small model

| Task | Who |
|---|---|
| Run thousands of inputs and judge by rules (invariants) — the expected value is fixed **when the input is generated** (a yardstick different from the code under test) | Python |
| Write, **in one batch** (about 100), inputs a person would really type and commonly gets wrong, with their expected values — given only the requirements document, not the code | a small-model subagent |
| A mismatch is a **candidate** — the small model's expected value may be wrong. Open the requirements document and judge each one | main conversation |

Model calls must not grow with the number of runs — generate in one batch and let Python do the running.
A Python generator only produces the shapes written in the requirements; producing inputs outside the imagination of whoever wrote the generator is the small model's part.

**When**: a feature that takes text written by people (amounts, dates, names, identifiers, free text, form fields) gets one round of small-model input generation on top of the Python invariants.
Input that rules describe completely (internal code values, fixed formats) needs Python only. Record the evidence with the manual-check procedure.

---

## The yardstick of a check — checks are wrong too

Checks and tests can pass and still be wrong. When writing one:

- **Build the expected value with a different yardstick.** Counting again with the same data and the same query as the code under test always passes
- **Read tables by their headers, and check the number of columns against the header too.** With only one of the two, the other defect goes unseen (one missing cell shifts the rest, and a check that reads by position compares the wrong values)
- **Before counting by a state value, count whether that state exists in the fixture.** If no row has the state, the mutants of that branch never bite
- **Keep one happy path.** Testing only the rejection paths does not notice a happy path that always fails
- **Test a failure in the middle.** A "correction" applied once more at the end hides mutants in the steps before it
- **A large restructuring that must not change behaviour** is not retyped by hand: run the old code as it is, freeze its results, and compare by hash
- **Break it on purpose and confirm it is caught.** If you added a guard, create the situation the guard should catch. A test that becomes meaningless also counts as a failure
- **With mutation results, look at exit code and pass count together with the failure count.** Counting failure lines only makes a run that stopped halfway look like "did not bite" — 0 and "did not run" are different
- **The other direction too:** a mutant that only makes the test time out is not "killed" — the test did not bite the rule, it just hung

## Review is measuring, not reading

Reading a long file a few lines at a time yields little; the defects at that layer come out by **measuring** (the `silent-review` agent counts writers against readers).
Write down what was looked at and to which depth (run, read only, not supported) with the table in [review-scope.md](review-scope.md) — the scope handed to a reviewer is set with the same table.

- **Predict the answer before running.** That is what turns into review sense
- **Write down what is right as intended, too.** The next person does not dig through the same place again
- **Write down wrong suspicions, too** — before concluding that something is missing, read that function from its first line
- **Dismiss false positives.** If it cannot be reproduced, do not fix it. Fixing a problem that does not exist only makes the code worse

---

## Before saying "verified"

- Was it run — not only read
- Compared with what is it called right — is there an assertion
- Is there a part that was not run — and if so, was that stated
- Did the tool really exist — or was it pretended

If any of the four is a no, write **"confirmed up to here, the rest unconfirmed"**, not "verified".
