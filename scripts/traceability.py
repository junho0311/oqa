# -*- coding: utf-8 -*-
"""**Is there a test that verifies each expected result of the spec — counted per item.**

For every `- E1 ...` line of the spec's "## Expected results", the test files of the repository are searched for the marker
`verifies: <spec name>/E1`. <spec name> is the spec file name without .md (e.g. 2026-01-15_login); the full file name
(2026-01-15_login.md) is accepted too. The file that carries the marker is matched against the test commands of
`.oqa/tests.json` (a whole word of the command must be that path or its module form) to learn which test it is, and
that test's pass / fail is attached.

States — never mixed
  no marker           no test verifies it
  outside test list   the marker exists but its file is in none of the commands of .oqa/tests.json (a test that does not run)
  pass · fail         result of running that test (--run) or the result left in the impact map (only when the map is not stale and no .py was modified after recording)
  not run             the result is unknown — also when every verifying test that ran passed but one of the files carrying the marker is run by no
                      test of .oqa/tests.json (the files are named in `not_run_files` of the item): the item is not verified by the other file alone
  unknown             a verifying test has a result that is neither pass nor fail (a value this tool does not know) — not read as a pass
  no number           there are expected results but no E number — not flattened to 0 items. An unnumbered line mixed into a numbered spec gets this verdict too
  duplicate number    two or more items carry the same E number — a tag cannot say which of them it proves (cannot judge; renumber the items)
  no expected results

The marker is **per file** — the whole test file must pass for a pass (which line of the file belongs to the item is not looked at).

Usage
  python traceability.py --repo . --spec .oqa/specs/2026-01-15_login.md [--run] [--json result.json]
Exit code
  0  every item is "pass"
  1  at least one item is "no marker", "fail" or "outside test list"
  2  cannot judge — the spec has no expected results or no E numbers, or an item is "no number", "duplicate number", "not run" or "unknown" (and none is in the group of exit code 1)
"""
import argparse
import json
import re
import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mutate import DEFAULT_FAIL_PATTERN         # noqa: E402 — failure pattern · test list · HEAD are one definition shared with the other tools
from impact import modified_py, stale_reason, run, test_list, read_map   # noqa: E402
from evidence import section, ITEM_PATTERN       # noqa: E402 — one definition shared with core.py
EXCLUDED_DIRS = {".git", "__pycache__", "node_modules", ".oqa"}
LIST_LINE = re.compile(r"^\s*[-*]\s+(.*\S)")


def items(spec_text):
    """(status, [(number or None, text)]) — status: present · no number · no expected results.
    An unnumbered line mixed into a numbered spec is returned as (None, text) too — so it does not silently drop out"""
    body = section(spec_text, "Expected results")
    if body is None or not body.strip():
        return "no expected results", []
    found = []
    for line in body.splitlines():
        m = ITEM_PATTERN.match(line)
        if m:
            found.append((m.group(1), m.group(2).strip()))
        elif LIST_LINE.match(line):
            found.append((None, LIST_LINE.match(line).group(1)))
    return ("present", found) if any(number for number, _ in found) else ("no number", [])


def find_markers(repo, spec_name):
    """{number: [test file path, relative to the repository, with forward slashes]} —`verifies: spec_name/E<number>` in the .py files of the repository"""
    pattern = re.compile(r"verifies:\s*%s(?:\.md)?/(E\d+)\b" % re.escape(spec_name))
    found = {}
    for path in repo.rglob("*.py"):
        if EXCLUDED_DIRS & set(path.relative_to(repo).parts):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in pattern.finditer(text):
            found.setdefault(m.group(1), set()).add(path.relative_to(repo).as_posix())
    return {number: sorted(files) for number, files in found.items()}


def command_words(command):
    """The words of a test command with `\\` turned into `/` and the quotes taken off (a quoted path with a space stays one word)"""
    command = command.replace("\\", "/")
    try:
        return shlex.split(command)
    except ValueError:                                   # an open quote — split at blanks and take the quote characters off
        return [word.strip("\"'") for word in command.split()]


def tests_for(tests, file, map_tests=None, repo=None):
    """Names of the tests that run that file. A test runs the file when any one of these holds:
    (1) a word of its command is the file path — as it is, with a leading `./`, with a pytest node id behind it (`path::test`), or (when the
        repository is given) as an absolute path that leads to that file inside the repository;
    (2) a word of its command is the module form of the path (tests.test_calc; a trailing .Class is fine);
    (3) the impact map recorded that the test executed lines of that file (this covers a command that runs a whole folder).
    Whole words only: the path was once looked for as a piece of text anywhere in the command, so the marker file `a.py` was credited to a test
    that runs `tests/data.py`."""
    module = file[:-3].replace("/", ".") if file.endswith(".py") else None
    root = Path(repo).resolve() if repo is not None else None
    found = []
    for test in tests:
        runs = file in ((map_tests or {}).get(test["name"]) or {})
        for word in ([] if runs else command_words(test["command"])):
            path = word.split("::")[0]
            while path.startswith("./"):
                path = path[2:]
            if path == file:
                runs = True
            elif module and "/" not in word and not word.endswith(".py") and (word == module or word.startswith(module + ".")):
                runs = True
            elif root is not None and path.endswith("/" + file) and (path.startswith("/") or re.match(r"^[A-Za-z]:/", path)):
                try:
                    runs = Path(path).resolve().relative_to(root).as_posix() == file
                except (ValueError, OSError):
                    runs = False
            if runs:
                break
        if runs:
            found.append(test["name"])
    return found


def run_verdict(command, repo, fail_pattern):
    code, output = run(command, repo)
    return "fail" if code is None or code != 0 or re.search(fail_pattern, output) else "pass"


def trace(repo, spec, run_tests=False, fail_pattern=DEFAULT_FAIL_PATTERN, known_results=None):
    """known_results {name: pass|fail} — tests already run on the same original (the impact tests and the mutation baseline of report.py) are not run again. The number run is "tests_run" of the result"""
    text = spec.read_text(encoding="utf-8-sig", errors="replace")
    status, listed = items(text)
    result = {"spec": spec.stem, "status": status, "items": [], "tests_run": 0}
    if status != "present":
        return result
    markers = find_markers(repo, spec.stem)
    tests = test_list(repo)
    map_ = read_map(repo)
    # map results only when the map is not stale and no .py was modified after recording — the same yardstick as select
    map_results = map_.get("results", {}) if map_ and not stale_reason(map_, repo) and not modified_py(repo) else {}
    # the map used to find tests — only when it is not stale (same HEAD) and only tests whose command at recording time equals the command now. With a stale map,
    # or a test whose command changed after recording, a file that the test no longer runs would still be counted as run by that test. A map without recorded commands is not used
    commands_now = {t["name"]: t["command"] for t in tests}
    trusted_map = {name: files for name, files in (map_.get("tests") or {}).items() if (map_.get("commands") or {}).get(name) == commands_now.get(name) is not None} if map_ and not stale_reason(map_, repo) else {}
    ran = dict(known_results or {})
    numbers = [number for number, _ in listed]
    for number, content in listed:
        if number is None:
            result["items"].append({"id": "—", "text": content, "tests": [], "files": [], "verdict": "no number"})
            continue
        if numbers.count(number) > 1:
            # two items with one number (a copied line that was not renumbered): a tag `…/E1` cannot say which of them it proves — both used to pass on it
            result["items"].append({"id": number, "text": content, "tests": [], "files": [], "verdict": "duplicate number"})
            continue
        files = markers.get(number, [])
        names = sorted({name for f in files for name in tests_for(tests, f, trusted_map, repo)})
        if not files:
            verdict = "no marker"
        elif not names:
            verdict = "outside test list"
        else:
            verdicts = []
            for name in names:
                if run_tests:
                    if name not in ran:
                        test = next(t for t in tests if t["name"] == name)
                        ran[name] = run_verdict(test["command"], repo, test.get("fail_pattern") or fail_pattern)   # the pattern set per test — same as impact.py
                        result["tests_run"] += 1
                    verdicts.append(ran[name])
                else:
                    verdicts.append(map_results.get(name, "not run"))
            # only an explicit "pass" of every verifying test is a pass — a value this tool does not know (handed over by another tool, or left in the map) is "unknown", never a pass
            verdict = ("fail" if "fail" in verdicts else "unknown" if any(v not in ("pass", "not run") for v in verdicts)
                       else "not run" if "not run" in verdicts or not verdicts else "pass")
        # A tagged file that no listed test runs was never run: the tests of the other tagged files say nothing about it. The item used to pass on
        # the strength of the file that is listed alone — it is "not run" now (cannot judge), and the files are named in `not_run_files`
        not_run_files = [f for f in files if not tests_for(tests, f, trusted_map, repo)]
        if verdict == "pass" and not_run_files:
            verdict = "not run"
        result["items"].append({"id": number, "text": content, "tests": names, "files": files, "not_run_files": not_run_files, "verdict": verdict})
    return result


def exit_code(result):
    if result["status"] != "present":
        return 2
    verdicts = [item["verdict"] for item in result["items"]]
    if any(v in ("no marker", "fail", "outside test list") for v in verdicts):
        return 1
    return 0 if verdicts and all(v == "pass" for v in verdicts) else 2      # "not run" · "no number" · "unknown" — and any verdict this function does not know — are cannot judge, never 0


def show(result):
    print("spec %s — %s" % (result["spec"], result["status"]))
    if result["status"] != "present":
        print("  no expected results with an E number, so they cannot be counted per item (this is not 0 items)")
        return
    for item in result["items"]:
        not_run = ("  · tagged file(s) no listed test runs: " + ", ".join(item["not_run_files"])) if item.get("not_run_files") and item["tests"] else ""
        print("  %-4s %-17s %s%s%s" % (item["id"], item["verdict"], item["text"][:60], ("  ← " + ", ".join(item["tests"])) if item["tests"] else "", not_run))
    counts = {}
    for item in result["items"]:
        counts[item["verdict"]] = counts.get(item["verdict"], 0) + 1
    print("  " + " · ".join("%s %d" % pair for pair in counts.items()))


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Match the expected results of the spec against the markers in the tests")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--spec", required=True)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--fail-pattern", default=DEFAULT_FAIL_PATTERN)
    parser.add_argument("--json")
    args = parser.parse_args(argv)
    repo = Path(args.repo).resolve()
    spec = Path(args.spec)
    spec = spec if spec.is_absolute() else (repo / spec)
    if not spec.is_file():
        print("no spec file:", spec)
        return 2
    result = trace(repo, spec, args.run, args.fail_pattern)
    show(result)
    if args.json:
        Path(args.json).write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return exit_code(result)


if __name__ == "__main__":
    sys.exit(main())
