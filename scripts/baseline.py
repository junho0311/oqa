# -*- coding: utf-8 -*-
"""**Baseline of a repository seen for the first time (no change) — one table of how much the tests pass through and how much they catch, per file and per function.**

    python baseline.py --repo <root> [--files a.py folder …] [--sample 6] [--no-mutation] [--timeout 180] [--top 20]

Why: impact tests, mutation, tracing and the report all need a change and a spec to run. On a system seen for the first time
it is not even known "where are the tests weak". So the whole repository is measured once without any change.

What it does
1. When the impact map (.oqa/impact-map.json) is missing or stale (impact.stale_reason) it is recorded anew with impact.record. A usable map is used as it is
2. Per file and per function: "number of tests passing through · ratio of covered lines". Function names come from _function_at (Class.function).
   The denominator of the ratio is the **lines that can run** — the set of lineno of ast statements (blank lines · comments · statements that are
   only a string (docstring) excluded). For the lines of a function the def · class header lines are left out (merely importing the module
   passes through the header lines — counting them makes a function that was never called look covered)
3. Mutation sample — for each file mutate.py runs as a subprocess with `--range all --max k --tests-file …` and its `--json` result is read.
   `--map …` is added only when there is a usable map (a stale map is not passed on — then every test runs for each mutant).
   Kill rate = killed / (killed + survived). Crashed (unknown whether a test caught it) and gap (no test passes through — already counted in
   coverage) are not in the denominator
4. Risk map — score = uncovered lines + covered lines × (1 − kill rate) = "estimate of weak lines". Larger for a bigger size, lower coverage and a lower kill rate.
   **A value that was never measured is None ("not measured"), not 0** — a row whose kill rate could not be measured gets no score and is listed
   separately below (by uncovered lines). A row with 0 covered lines has score = number of executable lines regardless of the kill rate (the second term is 0)
5. `<repo>/.oqa/baseline.json` + the table on standard output

Exit code: 0 written · 1 written, but a test failed while the impact map was recorded (the coverage comes from a failing run — the Baseline
evidence is `fail`) · 2 the map cannot be used (no test list · recording failed, etc.) · no targets
The Baseline evidence is `pass` only for a baseline whose code fingerprint was measured and stayed the same from start to end; otherwise `unknown`.
What it cannot see: the same things impact.py cannot see — python -I processes, tests that run in a copy outside the repository, and code that is not Python.
The mutation sample is k per file, so the kill rate of a function often rests on 0 to 2 samples ("not measured" is common). Header lines such as
`try:` may raise no line event depending on the Python version, so coverage can come out slightly low.
"""
import argparse
import ast
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import impact                                         # noqa: E402 — record · read_map · stale_reason · head · test_list
from mutate import DEFAULT_FAIL_PATTERN               # noqa: E402
from report import TEST_FILE                          # noqa: E402 — the yardstick for "test file" is the one definition the report uses (tests/ · test_*.py · *_test.py)

NOT_MEASURED = None


def _function_at(text):
    """line → name of the innermost function holding that line (Class.function). "<module>" outside any function"""
    tree = ast.parse(text)
    spots = {}

    def walk(node, prefix):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = (prefix + "." if prefix else "") + child.name
                if not isinstance(child, ast.ClassDef):
                    for n in range(child.lineno, (child.end_lineno or child.lineno) + 1):
                        spots[n] = name
                walk(child, name)
            else:
                walk(child, prefix)
    walk(tree, "")
    return lambda line: spots.get(line, "<module>")


def _inside(repo, relative):
    try:
        (repo / relative).resolve().relative_to(repo)
        return True
    except ValueError:
        return False


def _target_files(repo, names):
    found = []
    for f in names:
        if not _inside(repo, f):
            raise SystemExit("outside the repository: %s" % f)
        path = (repo / f).resolve()
        if path.is_dir():
            found += sorted(p for p in path.rglob("*.py") if "__pycache__" not in p.parts)
        elif path.is_file():
            found.append(path)
        else:
            raise SystemExit("no such target: %s" % f)
    return [p.relative_to(repo) for p in found]


def _tracked_files(repo):
    done = subprocess.run(["git", "-C", str(repo), "-c", "core.quotepath=false", "ls-files", "-z"], capture_output=True)
    if done.returncode != 0:
        raise SystemExit("not a git repository — targets are taken only from files git tracks: %s" % repo)
    return [Path(f) for f in done.stdout.decode("utf-8", "replace").split("\0") if f]


def executable_lines(text):
    """(set of executable lines of the file, {function name: set of executable lines}). def · class header lines are not put into a function"""
    tree = ast.parse(text)
    spot = _function_at(text)
    file_lines, function_lines = set(), {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            function_lines.setdefault(spot(node.lineno), set())  # a function whose body is only a string is listed too (0 lines — the ratio is None)
        if not isinstance(node, ast.stmt):
            continue
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            continue                                             # docstring · a statement that is only a string — it does not run
        file_lines.add(node.lineno)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue                                             # header line — merely importing passes through it
        owner = spot(node.lineno)
        if owner != "<module>":
            function_lines.setdefault(owner, set()).add(node.lineno)
    return file_lines, function_lines


def measure_coverage(lines, passed_table):
    """lines: set of executable lines, passed_table: {test name: set of lines it passed through in that file} → (number of tests passing through, number of covered lines, ratio | None)"""
    covered = set()
    through = 0
    for passed in passed_table.values():
        hit = lines & passed
        if hit:
            through += 1
            covered |= hit
    return through, len(covered), (round(len(covered) / len(lines), 3) if lines else NOT_MEASURED)


def kill_counts(mutants):
    counts = {k: sum(1 for m in mutants if m["verdict"] == k) for k in ("killed", "survived", "crashed", "gap")}
    denominator = counts["killed"] + counts["survived"]
    return counts, (round(counts["killed"] / denominator, 3) if denominator else NOT_MEASURED)


def score(line_count, covered_count, kill_rate):
    """Estimate of weak lines = uncovered lines + covered lines × (1 − kill rate). None when the kill rate cannot be measured — it is not flattened to 0.
    But with 0 covered lines the second term is 0, so the score is fixed regardless of the kill rate (so that a function no test passes through does not sink below the "not measured" group).
    A row without any executable line and without a measured kill rate has nothing that was measured: None, not a score of 0.0 (which reads as "no weak line")"""
    if covered_count == 0:
        if line_count == 0 and kill_rate is None:
            return NOT_MEASURED
        return float(line_count)
    if kill_rate is None:
        return NOT_MEASURED
    return round((line_count - covered_count) + covered_count * (1 - kill_rate), 1)


def test_command_files(tests):
    """.py files named in the commands of tests.json (relative to the repository root, with /)"""
    found = set()
    for test in tests:
        for f in re.findall(r"[^\s\"']+\.py\b", test["command"]):
            f = f.replace("\\", "/")
            found.add(f[2:] if f.startswith("./") else f)
    return found


def pick_targets(repo, names, tests):
    command_files = test_command_files(tests)
    if names:
        candidates = _target_files(repo, names)
    else:
        candidates = _tracked_files(repo)
    found = []
    for f in candidates:
        relative = f.as_posix()
        if not relative.endswith(".py") or TEST_FILE.search(relative) or relative in command_files or not (repo / f).is_file():
            continue
        found.append(relative)
    return sorted(set(found))


def prepare_map(repo, fail_pattern):
    """(map, recorded?, stale reason) — recorded when missing or stale. map is None when it still cannot be read after recording"""
    map_ = impact.read_map(repo)
    reason = "missing" if map_ is None else impact.stale_reason(map_, repo)
    if reason is None:
        return map_, False, None
    print("impact map is %s — recording a new one" % ("missing" if map_ is None else "stale (%s)" % reason))
    map_file = repo / ".oqa" / "impact-map.json"
    before = map_file.stat().st_mtime_ns if map_file.is_file() else None
    impact.record(repo, fail_pattern)
    # when recording did not write the map file anew (the old file is still there) it is the old map — the lines may disagree even with the same HEAD. None is returned so the run stops
    if not map_file.is_file() or (before is not None and map_file.stat().st_mtime_ns == before):
        return None, True, "could not record again"
    map_ = impact.read_map(repo)
    return map_, True, (None if map_ is None else impact.stale_reason(map_, repo))


def mutation_sample(repo, file, tests, k, timeout, fail_pattern, map_path):
    """(kill counts | None, list of mutants, state text). State: "measured" · "not measured (baseline failed)" · "not measured (generated 0)" · "not measured (mutation error)" """
    with tempfile.TemporaryDirectory(prefix="oqa_baseline_") as tmp:
        out = Path(tmp) / "mutation.json"
        tests_path = Path(tmp) / "tests.json"
        tests_path.write_text(json.dumps(tests, ensure_ascii=False), encoding="utf-8")
        args = [sys.executable, str(HERE / "mutate.py"), "--repo", str(repo), "--files", file, "--range", "all", "--max", str(k),
                "--timeout", str(timeout), "--fail-pattern", fail_pattern, "--json", str(out), "--tests-file", str(tests_path)]
        for test in tests:
            args += ["--test", test["command"]]
        if map_path:
            args += ["--map", str(map_path)]
        done = subprocess.run(args, capture_output=True, env=dict(os.environ,PYTHONIOENCODING="utf-8"))
        result = json.loads(out.read_text(encoding="utf-8")) if out.is_file() else None
    if done.returncode == 2 or (result or {}).get("baseline") == "fail":
        return None, [], "not measured (baseline failed)"
    if result is None or done.returncode not in (0, 1):
        tail = (done.stdout + done.stderr).decode("utf-8", "replace").strip().splitlines()
        return None, [], "not measured (mutation error: %s)" % ((tail or ["?"])[-1][:100])
    if not result.get("mutants"):
        return None, [], "not measured (generated 0)"
    return result.get("counts"), result["mutants"], "measured"


def write(repo, result):
    (repo / ".oqa").mkdir(exist_ok=True)
    path = repo / ".oqa" / "baseline.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def show(value):
    return "not measured" if value is None else ("%.0f%%" % (value * 100) if isinstance(value, float) else str(value))


def rank(rows):
    """Rows with a score by larger score first → below them the rows whose kill rate is "not measured" by more uncovered lines first (not flattened to 0 and mixed in)"""
    measured = sorted([r for r in rows if r["score"] is not None], key=lambda r: (-r["score"], -r["executable_lines"], r["file"], r.get("function", "")))
    unmeasured = sorted([r for r in rows if r["score"] is None], key=lambda r: (-(r["executable_lines"] - r["covered_lines"]), -r["executable_lines"], r["file"], r.get("function", "")))
    for i, r in enumerate(measured + unmeasured, 1):
        r["rank"] = i
    return measured + unmeasured


def reason_only(state):
    """The mutation state without its leading "not measured" — for a place where the kill rate printed next to it already says "not measured"
    (`not measured (generated 0)` → `generated 0`). A measured state is returned as it is"""
    return state[len("not measured ("):-1] if state.startswith("not measured (") and state.endswith(")") else state


def print_table(title, rows, top):
    print("\n── %s (top %d) ──" % (title, min(top, len(rows))))
    locations = [r["file"] + ((" · " + r["function"]) if r.get("function") else "") for r in rows[:top]]
    width = min(72, max([len("location")] + [len(text) for text in locations]))      # wide enough for the names shown, so function names are not cut off
    row_format = "%3s  %-" + str(width) + "s %5s %5s %12s %12s %12s  %s"
    print(row_format % ("#", "location", "lines", "tests", "coverage", "kill rate", "score", "mutation sample"))
    for r, location in zip(rows[:top], locations):
        # the kill-rate column already says "not measured"; this column gives only the reason
        note = reason_only(r["mutation"])
        print(row_format % (r["rank"], location[:width], r["executable_lines"], r["tests_through"], show(r["coverage"]), show(r["kill_rate"]),
                            "not measured" if r["score"] is None else "%.1f" % r["score"], note))


def main(argv=None):
    parser = argparse.ArgumentParser(description="baseline of a repository seen for the first time — coverage · kill rate · risk map per file and per function")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--files", nargs="+", help="target .py files · folders (relative to the repository root). Without it, the tracked .py files that are not test files")
    parser.add_argument("--sample", type=int, default=6, help="number of mutants to run per file (mutate.py --max)")
    parser.add_argument("--no-mutation", action="store_true", help="skip the mutation step — the kill rate column is \"not measured\"")
    parser.add_argument("--timeout", type=int, default=180, help="time limit of one test command (seconds)")
    parser.add_argument("--fail-pattern", default=DEFAULT_FAIL_PATTERN)
    parser.add_argument("--top", type=int, default=20, help="number of rows to print in the table")
    args = parser.parse_args(argv)
    repo = Path(args.repo).resolve()
    started = time.time()
    import evidence
    start_fingerprint = evidence.fingerprint(repo)      # the code before measuring — when it differs from the one at the end, the code changed while measuring
    try:
        tests = impact.test_list(repo)
        map_, recorded, reason = prepare_map(repo, args.fail_pattern)
    except SystemExit as e:
        print("cannot use the impact map: %s" % e)
        return 2
    if map_ is None:
        print("could not record the impact map — coverage cannot be measured")
        return 2
    if map_.get("HEAD") != impact.head(repo):
        # when the HEAD of the map still differs from now after recording again, the old map is still there — measuring the coverage of the code now with those line numbers is wrong.
        # A map that is stale because it was "recorded with modified .py files" holds lines just measured with those files, so it is used for coverage (only not passed on to mutation)
        print("the impact map differs from the current HEAD (could not record again) — coverage cannot be measured")
        return 2
    try:
        targets = pick_targets(repo, args.files, tests)
    except SystemExit as e:
        print("could not determine the targets: %s" % e)
        return 2
    if not targets:
        print("no target .py (test files are excluded)")
        return 2
    map_path = repo / ".oqa" / "impact-map.json" if reason is None else None      # a stale map is not passed on to mutation (the rule of mutate.py)

    file_rows, function_rows = [], []
    for file in targets:
        text = (repo / file).read_text(encoding="utf-8")
        try:
            file_lines, function_lines = executable_lines(text)
        except SyntaxError as e:
            file_rows.append({"file": file, "executable_lines": 0, "covered_lines": 0, "tests_through": 0, "coverage": NOT_MEASURED, "kill_rate": NOT_MEASURED, "counts": None, "score": NOT_MEASURED,
                              "mutation": "not measured (syntax error %s)" % e.lineno})
            continue
        passed_table = {name: set(files.get(file, [])) for name, files in map_["tests"].items()}
        through, covered_count, coverage = measure_coverage(file_lines, passed_table)
        if args.no_mutation:
            counts, mutants, state = None, [], "not measured (--no-mutation)"
        else:
            counts, mutants, state = mutation_sample(repo, file, tests, args.sample, args.timeout, args.fail_pattern, map_path)
        kill_rate = kill_counts(mutants)[1] if state == "measured" else NOT_MEASURED
        # show(kill_rate) already says "not measured" — the state adds only its reason (it used to print `not measured (not measured (generated 0))`)
        print("%-40s lines %d · tests %d · coverage %s · killed %s (%s)" % (file, len(file_lines), through, show(coverage), show(kill_rate), reason_only(state)))
        file_rows.append({"file": file, "executable_lines": len(file_lines), "covered_lines": covered_count, "tests_through": through, "coverage": coverage, "kill_rate": kill_rate, "counts": counts,
                          "score": score(len(file_lines), covered_count, kill_rate), "mutation": state})
        spot = _function_at(text)
        for function, lines in function_lines.items():
            f_through, f_covered, f_coverage = measure_coverage(lines, passed_table)
            own = [m for m in mutants if spot(m["line"]) == function]
            f_counts, f_kill_rate = kill_counts(own)
            if state != "measured":
                f_state = state
            elif f_kill_rate is None:
                f_state = "not measured (sample %d — none killed or survived)" % len(own) if own else "not measured (not in sample)"
            else:
                f_state = "measured (sample %d)" % len(own)
            function_rows.append({"file": file, "function": function, "executable_lines": len(lines), "covered_lines": f_covered, "tests_through": f_through, "coverage": f_coverage, "kill_rate": f_kill_rate,
                                  "counts": f_counts if own else None, "score": score(len(lines), f_covered, f_kill_rate), "mutation": f_state})
    file_rows, function_rows = rank(file_rows), rank(function_rows)
    untested = [{"file": r["file"], "function": r["function"], "executable_lines": r["executable_lines"]} for r in function_rows if r["tests_through"] == 0 and r["executable_lines"]]
    # Tests that failed while the map was recorded (impact.record says so and returns 1 — that answer used to be dropped here): a test that stops
    # early does not pass through the lines after that point, so the coverage below is not a baseline of working tests. Read from the map itself,
    # so a usable map recorded earlier with a failing test is caught too
    failed_tests = sorted(name for name, value in (map_.get("results") or {}).items() if value != "pass")
    result = {"created": time.strftime("%Y-%m-%d %H:%M"), "HEAD": impact.head(repo), "map_created": map_.get("created"), "map_HEAD": map_.get("HEAD"),
              "map_recorded": recorded, "map_stale": reason, "failed_tests": failed_tests, "sample": None if args.no_mutation else args.sample,
              "score_formula": "uncovered lines + covered lines × (1 − kill rate) — None (not measured) when the kill rate cannot be measured",
              "by_file": file_rows, "by_function": function_rows, "untested_functions": untested,
              "risk_ranking": [{"rank": r["rank"], "file": r["file"], "function": r["function"], "score": r["score"]} for r in function_rows],
              "seconds": round(time.time() - started, 1)}
    path = write(repo, result)
    print("\nimpact map: %s (HEAD %s)%s%s" % (map_.get("created"), (map_.get("HEAD") or "?")[:7], " · newly recorded in this run" if recorded else "",
                                             " · **stale: %s**" % reason if reason else ""))
    print_table("risk map — files", file_rows, args.top)
    print_table("risk map — functions", function_rows, args.top)
    print("\nfunctions that no test passes through: %d%s" % (len(untested), (" — " + ", ".join("%s · %s" % (r["file"], r["function"]) for r in untested[:12])) if untested else ""))
    print("score = estimate of weak lines (uncovered lines + covered lines × (1 − kill rate)). \"not measured\" is not 0 — rows whose kill rate could not be measured are listed below without a score")
    print("→", path)
    # evidence — the baseline of this code (fingerprint) was measured. Core's Baseline check compares this line with the fingerprint now
    # when the code changed while measuring (start and end fingerprints differ) it is unknown which code the baseline belongs to → fingerprint "unknown"
    # — and then the result is "unknown" too: a `pass` that belongs to no code is not evidence, and must not look like one in the file
    end_fingerprint = evidence.fingerprint(repo)
    measured = "files %d · functions %d · functions no test passes through %d" % (len(file_rows), len(function_rows), len(untested))
    if failed_tests:
        print("\n%d test(s) failed while the impact map was recorded: %s — the coverage above comes from a failing run (a test that stops early does not pass through the lines after that point). Fix them and take the baseline again"
              % (len(failed_tests), ", ".join(failed_tests)))
    if not (start_fingerprint and end_fingerprint):
        value, basis, fingerprint_value = "unknown", "the code fingerprint could not be measured — which code this baseline belongs to is unknown · " + measured, "unknown"
    elif start_fingerprint != end_fingerprint:
        value, basis, fingerprint_value = "unknown", "the code changed while the baseline was measured — which code it belongs to is unknown · " + measured, "unknown"
    elif failed_tests:
        value, basis, fingerprint_value = "fail", "tests failed while the impact map was recorded: %s · %s" % (", ".join(failed_tests), measured), end_fingerprint
    else:
        value, basis, fingerprint_value = "pass", measured, end_fingerprint
    if value != "pass":
        print("Baseline evidence: %s — %s" % (value, basis))
    evidence.record_quietly(repo, "Baseline", value, basis, fingerprint_value, "baseline.py")
    return 1 if failed_tests else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
