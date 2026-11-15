# -*- coding: utf-8 -*-
"""**Manual mutation — a person picks the fixed spot, reverts (breaks) it, and sees whether its repro test bites.**

    python manual_mutate.py --repo <root> --list <mutation list.json> [--jobs 4] [--timeout 600]

List: [{"name": "evidence order decided by time", "file": "src/calc.py", "pairs": [["old text", "new text"], ...], "test": "calc"}, ...]
  test — a test name of `.oqa/tests.json` (a case is "test:case") or a command text. The old text of a pair must appear in that file **exactly once**

The mutation tool (mutate.py) lets the machine break every changed line without exception, and this one aims at "what goes wrong when this fix is missing" — use both.
It is a tool rather than a throw-away script because it works on copies and nothing has to be restored by hand, and because a script that skips
the baseline counts every spot as a false "killed" when the test is already failing in the copy. So
- **before** breaking anything the test is run in the copy to see that it passes. If it does not, the mutations of that test are not counted and the exit code is 2
- the yardstick for "killed" is the same as mutate.py — a failure line, or ending with an error without a failure line. A timeout (crashed) is not killed
- the copies are removed at the end, read-only files included
Exit code: 0 all killed · 1 survived or crashed present · 2 baseline failed · list error (including a pair that could not be replaced)
"""
import argparse
import json
import queue
import shutil
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import evidence                                   # noqa: E402 — fingerprint · evidence record (Manual Mutation Test)
import mutate                                     # noqa: E402 — run_tests (stops on a kill) · classify · DEFAULT_FAIL_PATTERN · removing copies
import impact                                     # noqa: E402 — test_list (expands cases)


def replace_pairs(text, pairs):
    """(replaced text | None, why it could not be replaced) — every old text must appear exactly once (0 times or several times means it is unknown which spot was broken)"""
    for old, new in pairs:
        count = text.count(old)
        if count != 1:
            return None, "old text appears %d times: %s" % (count, old[:60])
        text = text.replace(old, new)
    return text, ""


def main(argv=None):
    parser = argparse.ArgumentParser(description="Revert chosen spots and see whether the repro test bites")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--list", required=True)
    parser.add_argument("--jobs", type=mutate.jobs_arg, default="auto", help="a number, or auto (default: half the CPUs, at most 8)")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--fail-pattern", default=mutate.DEFAULT_FAIL_PATTERN)
    args = parser.parse_args(argv)
    repo = Path(args.repo).resolve()
    try:
        mutations = json.loads(Path(args.list).read_text(encoding="utf-8-sig"))
        if not isinstance(mutations, list) or not mutations or not all(isinstance(m, dict) and m.get("name") and m.get("file") and m.get("pairs") and m.get("test") for m in mutations):
            raise ValueError("malformed")
        # every pair is [old, new] — a flat `"pairs": ["old", "new"]` ended in a traceback, or replaced single characters when both were two letters long
        if not all(isinstance(m["pairs"], list) and all(isinstance(p, list) and len(p) == 2 and all(isinstance(x, str) for x in p) and p[0] for p in m["pairs"]) for m in mutations):
            raise ValueError("malformed")
    except (OSError, ValueError):
        print("cannot read the mutation list or it is malformed ([{name, file, pairs: [[old, new]], test}] — at least one): %s" % args.list)
        return 2
    start_fingerprint = evidence.fingerprint(repo)
    table = {t["name"]: t for t in impact.test_list(repo)} if (repo / ".oqa" / "tests.json").is_file() else {}

    exclusive_lock = threading.Lock()                       # "exclusive" tests (tests using the same port or file) run one at a time even in different copies — same as mutate.py

    def command(test):
        t = table.get(test)
        return (t["command"], t.get("fail_pattern"), exclusive_lock if t.get("exclusive") else None) if t else (test, None, None)
    jobs = max(1, min(args.jobs, len(mutations)))
    copies = [Path(tempfile.mkdtemp(prefix="oqa_manual_mutate_")) / repo.name for _ in range(jobs)]
    results = []
    try:
        for copy in copies:
            shutil.copytree(repo, copy, ignore=mutate.COPY_IGNORE)
        # baseline — does it pass in the copy before anything is broken (with a test that already fails, whatever is broken becomes "killed")
        bad_baseline = {}
        for test in sorted({m["test"] for m in mutations}):
            failed, all_zero, timed_out, tail = mutate.run_tests([command(test)], copies[0], args.fail_pattern, args.timeout)
            print("baseline %-28s %s" % (test[:28], "pass" if not failed and all_zero else "fail — %s" % (tail or "timeout" if timed_out else tail)), flush=True)
            if failed or not all_zero:
                bad_baseline[test] = tail
        free_copies, print_lock = queue.Queue(), threading.Lock()
        for copy in copies:
            free_copies.put(copy)

        def one(m):
            if m["test"] in bad_baseline:
                return dict(m, verdict="baseline failed")
            copy = free_copies.get()
            try:
                target = copy / m["file"]
                try:
                    raw = target.read_bytes().decode("utf-8")
                except OSError:
                    return dict(m, verdict="not replaced", basis="no such file")
                new_text, reason = replace_pairs(raw.replace("\r\n", "\n"), m["pairs"])
                if new_text is None:
                    return dict(m, verdict="not replaced", basis=reason)
                if str(m["file"]).endswith(".py"):
                    # a replacement with a typo does not import at all: every test "fails", and that was counted as killed and recorded as a pass
                    # although the test was never asked about the rule (mutate.py leaves such a mutant out for the same reason)
                    try:
                        compile(new_text, str(m["file"]), "exec")
                    except (SyntaxError, ValueError) as error:
                        return dict(m, verdict="not replaced", basis="the replacement is not valid Python (%s%s)" % (
                            getattr(error, "msg", None) or error, (", line %s" % error.lineno) if getattr(error, "lineno", None) else ""))
                target.write_bytes(new_text.encode("utf-8"))
                try:
                    failed, all_zero, timed_out, tail = mutate.run_tests([command(m["test"])], copy, args.fail_pattern, args.timeout, stop_on_kill=True)
                finally:
                    target.write_bytes(raw.encode("utf-8"))
                return dict(m, verdict=mutate.classify(failed, all_zero, timed_out), basis=tail)
            finally:
                free_copies.put(copy)

        def one_and_print(m):
            r = one(m)
            with print_lock:
                print("%-15s %s%s" % (r["verdict"], r["name"], (" — " + str(r.get("basis") or "")[:90]) if r["verdict"] != "killed" else ""), flush=True)
            return r
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            results = list(pool.map(one_and_print, mutations))
    finally:
        for copy in copies:
            shutil.rmtree(copy.parent, onerror=mutate._unlock_and_remove)
    counts = {k: sum(1 for r in results if r["verdict"] == k) for k in ("killed", "survived", "crashed", "baseline failed", "not replaced")}
    summary = "total %d · killed %d · survived %d · crashed %d · baseline failed %d · not replaced %d" % (len(results), counts["killed"], counts["survived"], counts["crashed"], counts["baseline failed"], counts["not replaced"])
    print("\n" + summary)
    exit_code = 2 if counts["baseline failed"] or counts["not replaced"] else 1 if counts["survived"] or counts["crashed"] else 0
    # Evidence record — a manual mutation run is verification too, so it is recorded as the check "Manual Mutation Test"; the name is the list file name (the latest result
    # stays per list — a pass of one list does not cover a failure of another). When the code changed during the run or the fingerprint could not be measured, nothing is
    # recorded (it is unknown which code the result belongs to)
    end_fingerprint = evidence.fingerprint(repo)
    if start_fingerprint and start_fingerprint == end_fingerprint:
        evidence.record_quietly(repo, "Manual Mutation Test", {0: "pass", 1: "fail"}.get(exit_code, "unknown"), summary, start_fingerprint, "manual_mutate.py", name=Path(args.list).stem)
    elif start_fingerprint and end_fingerprint:
        # said, not passed over in silence: the check then stays "missing" in the verdict and nothing told the reader why
        print("evidence not recorded — the code fingerprint changed while this ran (%s → %s), so it is unknown which code the result belongs to. Run it again on code that stays as it is" % (start_fingerprint, end_fingerprint))
    else:
        print("evidence not recorded — the code fingerprint could not be measured (at the start or at the end), so it is unknown which code the result belongs to")
    return exit_code


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
