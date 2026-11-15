# -*- coding: utf-8 -*-
"""**For every surviving mutant, get a draft of the missing test and keep only the drafts that satisfy both conditions.**

    python drafting.py bundle --repo . --result mutants.json --out <folder>      for every survived mutant one bundle page (n.md) and the mutant information (n.json)
    python drafting.py check --repo . --bundle <folder>/n.json --candidate <folder>/n_candidate.py

Why: even when the mutation tool finds a "survived" mutant, writing the missing test was left to be done by hand.
Writing the draft is handed to a small model (a subagent), and **whether to accept it is decided by the machine** — however plausible a draft looks,
it is not used before both conditions have actually been run.

Conditions for accepting (both)
  1. On the original code: exit code 0 and no failure line (pass)
  2. On the code with that mutant applied: a failure line appears (killed) — dying from an exception or a timeout is not killed

`check` is stricter than mutate.py on purpose: mutate.py also counts a run that ends with an error as killed, but a draft must fail on the
mutant with a failure line. A draft that merely crashes is rejected.

The calling session hands each bundle page to a small-model subagent and collects the draft (no API key is used).
A received draft runs in a throw-away copy of the repository (a copy, not a sandbox) — the original repository is not modified. Moving an accepted draft into a test file is done by a reader, after reading it.
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutate                                     # noqa: E402 — apply · mutants · DEFAULT_FAIL_PATTERN · decode have one definition

FAIL_PATTERN = mutate.DEFAULT_FAIL_PATTERN
REQUEST_TEXT = """# Request for a missing test draft — {file}:{line}

The current tests did not catch the mutant below (a deliberate change to the code). Please write **one small Python test script that catches this mutant**.

## What was changed
- File: `{file}` (line {line}), kind of change: {kind}
- Original: `{original}`
- After the change: `{changed}`

## Original code around it (lines {start}~{end})
```python
{context}
```

## Rules for the test script
- It runs from the repository root as `python <script>`. Import the target module after `sys.path.insert(0, "<folder of that file>")` (folder: `{folder}`)
- Print one line per check: `print("  ok  <description>")` when it holds, `print("  FAIL  <description>")` when it does not — keep the two spaces before and after exactly
- On the original code every line must be "ok"; on the mutant above at least one "FAIL" line must appear
- No outside network, no servers, no new packages. Standard library only. Use tempfile for temporary files
- Answer with the single script only, in one ```python block
"""


def bundle(repo, result_path, out):
    result = json.loads(Path(result_path).read_text(encoding="utf-8"))
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    if result.get("baseline") != "pass":
        # when the baseline (original) failed or was not run, the answer is not "0 survived" but unknown
        print("mutation baseline is \"%s\" — survived mutants cannot be counted. Fix the tests first: %s" % (result.get("baseline", "unknown"), str(result.get("basis") or "")[:200]))
        return 2
    survived = [m for m in result.get("mutants", []) if m.get("verdict") == "survived"]
    if not survived:
        print("0 survived mutants — no bundle to write (mutants run %d)" % len(result.get("mutants", [])))
        return 0
    for n, m in enumerate(survived, 1):
        if "spot" not in m:
            print("the mutation result has no spot information — run mutate.py again")
            return 2
        text = (repo / m["file"]).read_text(encoding="utf-8")
        reason = stale(text, m)
        if reason:
            print("bundle %d (%s:%d) — %s. Run mutate.py again" % (n, m["file"], m["line"], reason))
            return 2
        lines = text.splitlines()
        changed_text = mutate.apply(text, m["spot"])
        start, end = max(1, m["line"] - 12), min(len(lines), m["line"] + 12)
        changed_line = changed_text.splitlines()[m["line"] - 1] if m["line"] - 1 < len(changed_text.splitlines()) else "(line removed)"
        (out / ("%d.md" % n)).write_text(REQUEST_TEXT.format(
            file=m["file"], line=m["line"], kind=m["kind"], original=lines[m["line"] - 1].strip(), changed=changed_line.strip(),
            start=start, end=end, context="\n".join(lines[start - 1:end]), folder=str(Path(m["file"]).parent).replace("\\", "/")), encoding="utf-8")
        (out / ("%d.json" % n)).write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")
    print("bundles %d → %s" % (len(survived), out))
    return 0


def stale(text, m):
    """Why that mutant cannot be rebuilt on the current file — None when there is no reason. Same safeguards as mutate.py (file hash · syntax · same text)"""
    if m.get("file_hash") and mutate.file_hash(text) != m["file_hash"]:
        return "the file changed after the mutant was generated, so the spot shifted (stale bundle)"
    if not m.get("file_hash"):
        return "the result has no file hash (a result of an old version) — unknown whether it is stale"
    changed = mutate.apply(text, m["spot"])
    if changed == text:
        return "the mutant does not change the text"
    try:
        compile(changed, m["file"], "exec")
    except SyntaxError:
        return "the mutant produces a syntax error"
    return None


def _run(candidate, cwd, timeout):
    try:
        done = subprocess.run([sys.executable, str(candidate)], cwd=str(cwd), capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, "timeout"
    return done.returncode, mutate.decode(done.stdout or b"") + mutate.decode(done.stderr or b"")


def check(repo, bundle_path, candidate_path, timeout=120):
    m = json.loads(Path(bundle_path).read_text(encoding="utf-8"))
    reason = stale((repo / m["file"]).read_text(encoding="utf-8"), m)
    if reason:
        print("rejected — " + reason)
        return {"verdict": "rejected", "reason": reason}
    candidate = Path(candidate_path).resolve()
    work = Path(tempfile.mkdtemp(prefix="oqa_drafting_")) / repo.name
    try:                                                   # the copy is inside too — the temporary folder is removed even when copying fails
        shutil.copytree(repo, work, ignore=mutate.COPY_IGNORE)
        code1, out1 = _run(candidate, work, timeout)
        original_passes = code1 == 0 and not re.search(FAIL_PATTERN, out1)
        target = work / m["file"]
        original_text = target.read_text(encoding="utf-8")
        target.write_text(mutate.apply(original_text, m["spot"]), encoding="utf-8")
        code2, out2 = _run(candidate, work, timeout)
        kills = bool(re.search(FAIL_PATTERN, out2))
    finally:
        shutil.rmtree(work.parent, ignore_errors=True)
    if original_passes and kills:
        verdict, reason = "accepted", "passes on the original · fails on the mutant"
    elif not original_passes:
        verdict, reason = "rejected", "does not pass on the original code: " + (out1.strip().splitlines() or ["(no output)"])[-1][:160]
    else:
        verdict, reason = "rejected", "does not catch the mutant" + (" — died without a failure line (crashed)" if code2 not in (0,) else "")
    print("%s — %s" % (verdict, reason))
    return {"verdict": verdict, "reason": reason}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Survived mutants → test draft bundles · checking a draft")
    parser.add_argument("action", choices=("bundle", "check"))
    parser.add_argument("--repo", required=True)
    parser.add_argument("--result", help="result of mutate.py --json (bundle)")
    parser.add_argument("--out", help="folder to write the bundles into (outside the repository — a scratch folder)")
    parser.add_argument("--bundle", help="n.json of a bundle (check)")
    parser.add_argument("--candidate", help="the received draft test file (check)")
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args(argv)
    repo = Path(args.repo).resolve()
    if args.action == "bundle":
        if not (args.result and args.out):
            parser.error("bundle needs --result and --out")
        return bundle(repo, args.result, args.out)
    if not (args.bundle and args.candidate):
        parser.error("check needs --bundle and --candidate")
    return 0 if check(repo, args.bundle, args.candidate, args.timeout)["verdict"] == "accepted" else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
