# OQA

**Evidence-based verification for code changes made with Claude Code.**

A coding agent says "done" from memory. OQA makes it prove it: every check leaves a line of evidence tied to a fingerprint of the code it looked at, and a small core decides `DONE` / `INCOMPLETE` from that evidence alone. Change one line afterwards and the evidence is stale — the verdict goes back to `INCOMPLETE` until the checks run again.

## What it does

- **Picks the checks a change needs.** `core.py plan` reads the working-tree diff and a short spec and lists the checks to run, with the reason for each.
- **Mutation testing on the changed lines.** Each changed line is broken on purpose in a throw-away copy; only the tests that actually execute that line are run, and at least one of them must fail for the mutant to count as killed. A mutant that survives is a line your tests do not pin down.
- **Requirement tracing.** Every numbered expected result in the spec (`E1`, `E2`, …) must have a test tagged `# verifies: <spec>/E1`, and that test must pass.
- **Reviews by separate subagents.** `silent-review` (five kinds of silent failure), `reasoning-review` and `reasoning-refute` (find a flaw by reasoning, then try to knock the finding down), `blind-check` (re-judge without seeing the conclusion). Findings go into a ledger; accepting one requires a reproduction test.
- **A verdict computed from evidence, not from the conversation.** `core.py verdict` matches evidence to the current code fingerprint, checks open findings and the user's approval, and exits 0 only for `DONE`.
- **First look at an unfamiliar repository.** `baseline.py` ranks files by coverage and by how many sampled mutants the existing tests miss.

## Requirements

- Python 3.10 or newer and git. Standard library only — nothing is downloaded or installed.
- The automated checks work on **Python** projects whose tests run from a command line (plain scripts, `unittest`, `pytest`). For other languages only the reviews, the findings ledger and the evidence/verdict layer apply.

## Install

```
/plugin marketplace add junho0311/oqa
/plugin install oqa@oqa
```

Then ask Claude to "verify this change with OQA", or follow the loop below yourself.

## The loop

```
python <oqa>/scripts/init.py --repo .                   # once: writes .oqa/tests.json (one entry per test file) and adds lines to .gitignore
#   ... commit .gitignore together with .oqa/tests.json (an uncommitted .gitignore makes every report "cannot judge") ...
#   ... write .oqa/specs/<name>.md (expected results, finish line), get the user's approval ...
python <oqa>/scripts/impact.py record --repo .          # on a clean commit, before editing: which test runs which line
#   ... change code, write tests ...
python <oqa>/scripts/core.py plan --repo .              # which checks this change needs
python <oqa>/scripts/report.py --repo . --spec .oqa/specs/<name>.md   # all mutants of the changed lines; prints a time estimate first and stops if it is over 30 min
python <oqa>/scripts/core.py verdict --repo .           # DONE (0) / INCOMPLETE (1) / UNKNOWN (2) / NEEDS DECISION (3)
```

What the last two commands print for a change whose tests pin every changed line:

```
**Verdict: all passed**
- Impact tests: changed files 3 · selected tests 1 / total 2 · passed 1
- Gaps: none
- Mutation (changed lines): generated 6 · run 6 · killed 6 · survived 0 · crashed 0 · gap 0 — test selection hunk level 6

Core verdict: DONE (exit code 0) — all 3 required check(s) have evidence for the code now (fingerprint 492ffbf87f25)
```

and for a change where one added line can be broken without any test failing:

```
- Added lines not confirmed — mutants on them survived: calc.py (1 survived)
- Mutation (changed lines): generated 2 · run 2 · killed 1 · survived 1 · crashed 0 · gap 0 — test selection hunk level

Core verdict: INCOMPLETE (exit code 1) — verification report: fail · incomplete 1 — survived mutants 1; failed check: Regression Test; failed check: Mutation Test
```

`<oqa>` is the folder this plugin is installed in. The skill (`skills/oqa/SKILL.md`) walks Claude through the same steps; `skills/oqa/references/verify.md` documents every check.

## What OQA keeps in your repository

Everything lives under `.oqa/`:

| File | Commit it? | Purpose |
|---|---|---|
| `tests.json` | yes | the test commands |
| `specs/*.md`, `current` | yes | specs with expected results, finish line, approval, and the written report |
| `findings.jsonl` | yes | review findings and how each was closed |
| `evidence.jsonl`, `impact-map.json`, `core.json`, `baseline.json` | no | machine-local state |

The scripts make no network calls and install nothing. They run your own test commands, in copies of your repository inside the system temp folder, and read git. The review agents are ordinary Claude Code subagents: when one runs, the code it reads goes to the model like any other Claude Code work.

## What it runs on your machine

Nothing here is hidden behaviour, but a verification tool executes code, so it is spelled out:

- **Your test commands**, exactly as written in `.oqa/tests.json`, through the system shell — in the repository and in throw-away copies of it under the system temp folder. Review that file like any other script in your repository.
- **A line recorder** while `impact.py record` and `baseline.py` run: a small `sitecustomize.py` is placed in a temp folder and put on `PYTHONPATH` for the test processes only, so each test's executed lines can be recorded. It is not written into your repository.
- **Test drafts**, when you use `drafting.py check`: the candidate test (written by you or by a model) is executed in a throw-away copy of the repository. A copy is not a sandbox — read a draft before checking it.
- **Process clean-up**: a test command that hangs or leaves child processes behind is ended together with its children (a job object and `taskkill` on Windows, the process group elsewhere).
- **git**, read-only except for a temporary index file used to compute the code fingerprint.

No network access, no downloads, no credentials, no telemetry, no hooks.

## Limits — read before relying on it

**What the method cannot see**

- **It measures tests, not truth.** A requirement nobody wrote down is not traced, and a wrong expected result that is tested faithfully still passes.
- **A tagged test that passes is not proof of a good test.** Tracing checks that the test tagged for a requirement exists and passes. Mutation looks at the changed lines with all tests together, so a weak tagged test is not flagged when another test kills the mutants.
- **Only what an operator can break is probed.** The operators are a modest set: comparisons, boolean conditions, constants, returns, removed statements, decorators. Added lines no operator touches count as "nothing to mutate" without a test — imports, docstrings, `pass`, `global`, `assert`, `break`, `print(...)`, the header line of a `def`, `class`, `try`, `with` or `for`, and a new file made only of such lines. Regex internals and message text need the hand-written lists of `manual_mutate.py`.
- **A file is test code or source by its name.** `test_*.py`, `*_test.py` and everything under a `tests/` folder is not mutated; the report names those files on a line of its own, so a real module called `speed_test.py` does not pass unnoticed.
- **It guards against honest mistakes, not against tampering.** Evidence, specs and the approval line are plain local text files; someone who edits them by hand can produce a `DONE`.
- **The reviewing subagents are the same model family as the author.** They catch real problems, but they are a weaker independent eye than another vendor's model or a human reviewer.

**Practical**

- **Mutation testing is slow.** Each mutant runs the tests that cover its line. Small, fast test files make it practical; one large slow test file makes every mutant cost that file's full run time. Split big test files or use the case markers described in `verify.md`.
- **Developed on Windows; the test suite also passes on Linux (Ubuntu under WSL).** macOS has not been tried.

**How well OQA itself is tested**

- **Its own tests, measured with itself.** Mutating every line of the scripts (not just changed lines) with OQA itself: 3,904 mutants, 88.0% killed overall; 92.8% in the code that decides verdicts (`core`, `evidence`, `spec`, `report`), 90-92% in `mutate` and `init`, 79-85% in the impact-map, tracing, findings, baseline and manual-mutation tools, 67% in `drafting`. The survivors in the verdict code were read one by one: those that could change a verdict or an exit code got a test, the rest are message text, defaults and statements whose removal changes nothing. The survivors in the other tools have not all been looked at.
- **Review found defects every time.** Four rounds of review by subagents each found real ones — wrong `DONE` paths and dead ends; the last two rounds found 8 and 19 that could be reproduced. Each was fixed with a test that fails when the fix is taken out, except the three that are limits of the method and are stated above. The count did not fall from round to round, so there are more: please report what you find.

## Layout

```
.claude-plugin/plugin.json
skills/oqa/            the skill, the spec template, reference docs
agents/                silent-review, reasoning-review, reasoning-refute, blind-check
scripts/               init, core, report, mutate, manual_mutate, impact, traceability, findings, baseline, drafting, evidence, spec
tests/                 the tools' own tests (plain scripts; run each with python)
```

## License

MIT
