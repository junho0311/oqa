# -*- coding: utf-8 -*-
"""OQA Core (scripts/core.py) — does the plan pick checks as the rule table says (E1), and does the verdict bundle evidence records · report · approval · independent review gate (E2).
`python tests/core_test.py`"""
import _harness  # noqa: F401,E402 — collects the temp folders in one place for this test and removes it at the end (first)
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = Path(__file__).resolve()
SCRIPTS = HERE.parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import core  # noqa: E402
from evidence import fingerprint  # noqa: E402

failures = 0


def check(ok, description, got=""):
    global failures
    failures += not ok
    print("  %s  %-60s %s" % ("ok" if ok else "FAIL", description, str(got)[:110]))


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", "-c", "core.autocrlf=false"] + list(args),
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


BASE_SPEC = "# Spec\n\n## What\nTidy the screen\n\n## Expected results\n- E1 the screen opens\n\n## Approval\n- 2099-01-01 · \"yes, go ahead\"\n\n## Results\n"
SPEC_FILE = ".oqa/specs/2099-01-01_a.md"


origin = None


def make_repo(name="proj", spec_text=BASE_SPEC, with_map=True):
    """Temp git repository — specs (current · spec) · first commit (README · ui · judging tool · hook) · impact map (optional).
    One git call takes a noticeable fraction of a second, so only the first one is built and the rest are copies (.oqa holds untracked new files —
    this also checks that core leaves .oqa/ out of the change)"""
    global origin
    if origin is None:
        origin = Path(tempfile.mkdtemp()) / "origin"
        for path, text in {"README.md": "# Guide\n", "ui/a.html": "<p>a</p>\n", "tools/score.py": "def tally(x):\n    return x + 1\n",
                           "hooks/scripts/h.py": "X = 1\n", ".gitignore": "__pycache__/\n"}.items():
            (origin / path).parent.mkdir(parents=True, exist_ok=True)
            (origin / path).write_text(text, encoding="utf-8")
        git(origin, "init", "-q")
        git(origin, "add", "-A")
        git(origin, "commit", "-q", "-m", "first")
    repo = Path(tempfile.mkdtemp()) / name
    shutil.copytree(origin, repo)
    (repo / ".oqa" / "specs").mkdir(parents=True)
    (repo / ".oqa" / "current").write_text("2099-01-01_a.md\n", encoding="utf-8")
    (repo / SPEC_FILE).write_text(spec_text, encoding="utf-8")
    if with_map:
        (repo / ".oqa").mkdir(exist_ok=True)
        (repo / ".oqa" / "impact-map.json").write_text('{"HEAD": "x", "tests": {}}', encoding="utf-8")      # impact.py takes only an object with a "tests" table as a map
    return repo


def write(repo, path, text):
    (repo / path).parent.mkdir(parents=True, exist_ok=True)
    (repo / path).write_text(text, encoding="utf-8")


def check_names(result):
    return {c["name"] for c in result.get("checks", [])}


def spec_hashes(repo):
    return {str(f): hashlib.sha1(f.read_bytes()).hexdigest() for f in sorted((repo / ".oqa" / "specs").rglob("*.md")) + [repo / ".oqa" / "current"]}


TOOLS_SPEC = BASE_SPEC.replace("## Expected results", "## Tools\n- when reviewing: blind review (blind-check)\n\n## Expected results")

import evidence as _ev  # noqa: E402

THREE_CHECKS = ("Regression Test", "Mutation Test", "Requirements Traceability")

def record(repo, kind, result="pass", fingerprint_value=None, **extra):
    return _ev.record(repo, kind, result, "recorded by the test", fingerprint_value, "core_test", **extra)

def prepare(spec_text=BASE_SPEC):
    """A repository with tools/score.py edited (→ Regression · Mutation · Traceability · Silent, + Independent Review Gate when the spec switches the gate on) + core.json"""
    repo = make_repo(spec_text=spec_text)
    write(repo, "tools/score.py", "def tally(x):\n    return x + 2\n")
    return repo, core.plan(repo)

def record_all(repo, skip=(), fingerprint_value=None):
    """One good evidence line per check + the verification report (Verification Report)"""
    for kind, result in [("Regression Test", "pass"), ("Mutation Test", "pass"), ("Requirements Traceability", "pass"), ("Silent Failure Review", "done"),
                         ("Verification Report", "pass"), ("Independent Review Gate", "approved")]:
        if kind not in skip:
            record(repo, kind, result, fingerprint_value)

def states(v):
    return {c["name"]: c["state"] for c in v.get("checks", [])}

TEST_HEAD = "import sys\nsys.path.insert(0, r'%s')\nimport _harness  # noqa\n" % Path(__file__).resolve().parent

import _harness as _h  # noqa: E402

GATE_SPEC_TABLE = BASE_SPEC + "\n## Independent review gate\n- Independent review gate: on (user 2099-01-01 \"use the review gate\")\n\n| round | date | gate | model · reasoning | open high · medium · low | verdict | result |\n|---|---|---|---|---|---|---|\n"
GATE_SPEC = BASE_SPEC + "\n## Independent review gate\n- Independent review gate: on (user 2099-01-01 \"use the review gate\")\n"



def finish():
    print("\nfailures: %d" % failures)
    return 1 if failures else 0


# Below are the case pieces — they do not run right away; _harness.run_cases runs only the picked ones (`--case name` · `--list-cases`). Cases do not use each other's variables
raise SystemExit(_harness.run_cases(__file__, globals(), finish))


# case: E1plan-01
print("── E1 plan: as the rule table says for each shape of change ──")
# case: E1plan-02
repo = make_repo()
write(repo, "ui/a.html", "<p>b</p>\n")
write(repo, "ui/b.css", "p{}\n")
before = spec_hashes(repo)
p = core.plan(repo)
check(check_names(p) == {"Regression Test", "E2E UI Test", "Requirements Traceability"},
      "ui files only (E numbers in the spec) → Regression · E2E UI · Traceability", check_names(p))
check(all(c["reason"] and c["condition"] for c in p["checks"]), "every check carries a reason · condition", [c["reason"] for c in p["checks"]])
check(any("ui/a.html" in c["reason"] for c in p["checks"] if c["name"] == "E2E UI Test"), "the E2E reason names the ui file")
saved = json.loads((repo / ".oqa" / "core.json").read_text(encoding="utf-8"))
check(set(saved) >= {"created", "HEAD", "change_fingerprint", "spec_file", "checks", "files"} and saved["spec_file"] == SPEC_FILE
      and saved["HEAD"] and sorted(saved["change_fingerprint"]["files"]) == ["ui/a.html", "ui/b.css"], ".oqa/core.json has created · HEAD · fingerprint · spec · checks · file kinds", saved.get("change_fingerprint"))
check(spec_hashes(repo) == before, "the spec files are not modified (only core.json)")
check({f["path"]: f["kind"] for f in p["files"]} == {"ui/a.html": "ui", "ui/b.css": "ui"}, "kind of ui files", p["files"])

# case: E1plan-03
repo = make_repo()
write(repo, "tools/score.py", "def tally(x):\n    return x + 2\n")
p = core.plan(repo)
check(check_names(p) == {"Regression Test", "Mutation Test", "Requirements Traceability", "Silent Failure Review"},
      "judging logic .py (tools/score.py) → Regression · Mutation · Traceability · Silent", check_names(p))
reason = next(c["reason"] for c in p["checks"] if c["name"] == "Silent Failure Review")
check("score" in reason and "code file name" in reason, "the Silent reason names the judging word it matched (score) and says it was found in the code file name", reason)
check(p["files"][0]["kind"] == "source code" and p["files"][0]["added"] == 1 and p["files"][0]["removed"] == 1, "source code · line counts (numstat)", p["files"])

# case: E1plan-04
repo = make_repo(spec_text=BASE_SPEC.replace("- E1 the screen opens\n", "none\n"))
for n in range(5):
    write(repo, "hooks/scripts/n%d.py" % n, "Y = %d\n" % n)
p = core.plan(repo)
check(check_names(p) == {"Regression Test", "Mutation Test", "Reasoning Review"}, "five new .py (hooks · no E) → Regression · Mutation · Reasoning", check_names(p))
check(all(f["kind"] == "source code" and f["status"] == "new" for f in p["files"]), "untracked new files are changes too · source code", p["files"][:1])
check("5 new .py" in next((c["reason"] for c in p["checks"] if c["name"] == "Reasoning Review"), ""), "the Reasoning reason has the number of new .py")

# case: E1plan-05
repo = make_repo()
write(repo, "hooks/scripts/n0.py", "Y = 0\n")
p = core.plan(repo)
check("Reasoning Review" not in check_names(p), "one new .py is not a structural change", check_names(p))

# case: E1plan-06
repo = make_repo()
write(repo, "README.md", "# Guide\nmore text\n")
p = core.plan(repo)
check(check_names(p) == {"Regression Test"}, "docs only → Regression only", check_names(p))
check(p["files"][0]["kind"] == "doc", "kind of a doc")

# case: E1plan-07
repo = make_repo(with_map=False)
write(repo, "README.md", "# Guide\nmore text\n")
p = core.plan(repo)
check(check_names(p) == {"Regression Test", "Baseline"}, "no impact map → Baseline is added", check_names(p))

# case: E1plan-08
repo = make_repo()
p = core.plan(repo)
check(p["checks"] == [] and p.get("reason", "").startswith("no changes"), "no changes → an empty list of checks together with the reason \"no changes\" (so it cannot be read as \"0 checks needed\")", p.get("reason"))
write(repo, SPEC_FILE, BASE_SPEC + "\nappended\n")
check(core.plan(repo)["checks"] == [], "a change in .oqa only is not a change")

# case: E1plan-09
repo = make_repo()
write(repo, "data/x.xyz", "?\n")
write(repo, "conf/a.yaml", "a: 1\n")
write(repo, "hooks/tests/z_test.py", "pass\n")
p = core.plan(repo)
kinds = {f["path"]: f["kind"] for f in p["files"]}
check(kinds == {"data/x.xyz": "unknown", "conf/a.yaml": "data/config", "hooks/tests/z_test.py": "test file"}, "an unknown suffix is \"unknown\" · config · test file", kinds)
check(check_names(p) == {"Regression Test", "Requirements Traceability"}, "test files · config only → no Mutation (config changed and there are E numbers, so Traceability is picked)", check_names(p))

# case: E1plan-10
repo = make_repo(spec_text=BASE_SPEC.replace("Tidy the screen", "Change the payment permissions"))
write(repo, "README.md", "x\n")
check("Blind Review" not in check_names(core.plan(repo)), "a permission word in the spec text alone is not Blind (such words appear in nearly every spec)")
# case: E1plan-11
repo = make_repo(spec_text=BASE_SPEC.replace("Tidy the screen", "Change the permission verdict — look again with a blind review (blind-check)"))
write(repo, "README.md", "x\n")
check("Blind Review" not in check_names(core.plan(repo)), "blind-review words in the \"What\" text of the spec, outside its \"Tools\" section, do not switch Blind on")
# case: E1plan-12
repo = make_repo(spec_text=TOOLS_SPEC)
write(repo, "README.md", "x\n")
check("Blind Review" in check_names(core.plan(repo)), "the spec's \"Tools\" names a blind review → Blind Review")
# case: E1plan-13
repo = make_repo(spec_text=BASE_SPEC + "- ran the blind review (blind-check)\n")
write(repo, "README.md", "x\n")
check("Blind Review" not in check_names(core.plan(repo)), "blind-review text written under \"## Results\" is not Blind")
# case: E1plan-14
repo = make_repo(spec_text=BASE_SPEC.replace("\"yes, go ahead\"", "\"approved — go ahead\""))
write(repo, "README.md", "x\n")
check("Blind Review" not in check_names(core.plan(repo)), "an approval word in the \"## Approval\" section alone is not Blind")
# case: E1plan-15
repo = make_repo(spec_text=BASE_SPEC.replace("Tidy the screen", "Fix the tally table"))
write(repo, "README.md", "x\n")
check("Silent Failure Review" not in check_names(core.plan(repo)), "a tally word in the spec text alone is not Silent")
# case: E1plan-16
repo = make_repo()
write(repo, "hooks/scripts/h.py", "".join("V%d = %d\n" % (n, n) for n in range(28)))
check("Silent Failure Review" not in check_names(core.plan(repo)), "29 changed lines of code (+28 -1) is below the Silent threshold (30)")
write(repo, "hooks/scripts/h.py", "".join("V%d = %d\n" % (n, n) for n in range(29)))
check("Silent Failure Review" in check_names(core.plan(repo)), "30 changed lines of code (+29 -1) → Silent")

# case: E1plan-17
repo = make_repo()
write(repo, "hooks/scripts/h.py", "import re\n" + "".join("A%d = re.match('x', s).group(0).split(',')\n" % n for n in range(4)))
check("Fuzz Test (LLM)" in check_names(core.plan(repo)), "a .py whose added lines parse input many times (re.match and split on four lines) → Fuzz")
# case: E1plan-18
repo = make_repo()
write(repo, "hooks/scripts/h.py", "import re\n" + "".join("A%d = re.match('x', s)\n" % n for n in range(2)))
check("Fuzz Test (LLM)" not in check_names(core.plan(repo)), "2 input-parsing hits on added lines is below the threshold (3)")
# case: E1plan-19
repo = make_repo()
write(repo, "hooks/scripts/h.py", "import re\n" + "".join("A%d = re.match('x', s)\n" % n for n in range(3)))
check("Fuzz Test (LLM)" in check_names(core.plan(repo)), "3 input-parsing hits on added lines is the threshold")

# case: E1plan-20
repo = make_repo()
write(repo, "ui/a.html", "".join("<p>%d</p>\n" % n for n in range(148)))
check("Reasoning Review" not in check_names(core.plan(repo)), "149 changed ui lines (+148 -1) is not a big change")
write(repo, "ui/a.html", "".join("<p>%d</p>\n" % n for n in range(149)))
check("Reasoning Review" in check_names(core.plan(repo)), "150 changed ui lines (+149 -1) → Reasoning")
# case: E1plan-21
repo = make_repo()
write(repo, "README.md", "".join("line %d\n" % n for n in range(400)))
check("Reasoning Review" not in check_names(core.plan(repo)), "400 changed lines of docs alone are not a structural change")
table = "## Changes\n| # | what |\n|---|---|\n" + "".join("| %d | a |\n" % n for n in range(1, 5))
repo = make_repo(spec_text=BASE_SPEC.replace("## Expected results", table + "\n## Expected results"))
write(repo, "README.md", "x\n")
check("Reasoning Review" not in check_names(core.plan(repo)), "a long \"Changes\" table in the spec is not Reasoning when this change is small (a small follow-up fix under a big spec must not switch it on)")
repo = make_repo(spec_text=BASE_SPEC.replace("## Expected results", table.replace("| 4 | a |\n", "") + "\n## Expected results"))
write(repo, "README.md", "x\n")
check("Reasoning Review" not in check_names(core.plan(repo)), "a \"Changes\" table of 3 rows is not either")

# case: E1plan-24
repo = make_repo(spec_text=GATE_SPEC_TABLE)
write(repo, "README.md", "x\n")
check("Independent Review Gate" in check_names(core.plan(repo)), "independent review gate on → Independent Review Gate")
repo_b = make_repo()
write(repo_b, "README.md", "x\n")
check("Independent Review Gate" not in check_names(core.plan(repo_b)), "gate off (no section) → none")

r = subprocess.run([sys.executable, str(SCRIPTS / "core.py"), "plan", "--repo", str(repo)], capture_output=True, text=True, encoding="utf-8", errors="replace")
check(r.returncode == 0 and "| Regression Test |" in r.stdout, "command-line plan → table · exit code 0", r.stdout[-120:])
r = subprocess.run([sys.executable, str(SCRIPTS / "core.py"), "plan", "--repo", tempfile.mkdtemp()], capture_output=True, text=True, encoding="utf-8", errors="replace")
check(r.returncode == 2 and "unknown" in r.stdout, "a folder that is not git → unknown · exit code 2", r.stdout.strip())


# case: E2verdict-01
print("── E2 verdict: the evidence records are matched against the fingerprint now ──")

# case: E2verdict-02
repo, p = prepare()
check(check_names(p) == {"Regression Test", "Mutation Test", "Requirements Traceability", "Silent Failure Review"}, "(setup) the four checks of the verdict test repository", check_names(p))
v = core.verdict(repo)
check(v["verdict"] == "INCOMPLETE" and set(v["missing_checks"]) == check_names(p) and v["report"] == "no report" and set(states(v).values()) == {"missing"},
      "no evidence at all → INCOMPLETE — every check \"missing\" · no report (manual checks are INCOMPLETE too)", v["message"])
record_all(repo, skip=("Independent Review Gate",))
v = core.verdict(repo)
check(v["verdict"] == "DONE" and v["exit_code"] == 0 and v["report"] == "all passed" and set(states(v).values()) == {"ok"} and v["fingerprint"] == fingerprint(repo),
      "good evidence with the fingerprint now for every check + report → DONE (0)", v["message"])
check(set(v) >= {"verdict", "exit_code", "message", "checks", "missing_checks", "stale_checks", "failed_checks", "to_confirm", "to_ask", "reasons", "report", "fingerprint"}, "keys of the verdict dict")

# case: E2verdict-03
repo, _ = prepare()
record_all(repo, skip=("Mutation Test",))
v = core.verdict(repo)
check(v["verdict"] == "INCOMPLETE" and v["exit_code"] == 1 and v["missing_checks"] == ["Mutation Test"], "one missing → INCOMPLETE · that check", v["message"])
record(repo, "Mutation Test", "fail")
v = core.verdict(repo)
check(v["verdict"] == "INCOMPLETE" and v["failed_checks"] == ["Mutation Test"] and not v["missing_checks"], "failed evidence → INCOMPLETE (failed check)", v["message"])
record(repo, "Mutation Test", "pass")
check(core.verdict(repo)["verdict"] == "DONE", "run again after the failure and pass → the latest evidence counts (DONE)")
record(repo, "Silent Failure Review", "unknown")
v = core.verdict(repo)
check(v["verdict"] == "NEEDS DECISION" and v["exit_code"] == 3 and any("Silent Failure Review — result unknown" in t for t in v["to_confirm"]), "the result of a check's evidence is unknown → NEEDS DECISION (3)", v["message"])

# editing one line of code makes all earlier evidence (tests · reviews · report) stale at once
# case: E2verdict-04
repo, _ = prepare()
record_all(repo, skip=("Independent Review Gate",))
write(repo, "tools/score.py", "def tally(x):\n    return x + 3\n")
v = core.verdict(repo)
check(v["verdict"] == "INCOMPLETE" and any("the change differs from the one planned" in t for t in v["reasons"]), "code edited again after the plan (not planned again) → \"the change differs from the one planned\"", v["reasons"])
core.plan(repo)
v = core.verdict(repo)
check(v["verdict"] == "INCOMPLETE" and set(v["stale_checks"]) == {"Regression Test", "Mutation Test", "Requirements Traceability", "Silent Failure Review"} and not v["missing_checks"]
      and v["report"] == "code changed after report", "editing the code makes test · review · report evidence all \"stale\" — without a rule per check", (v["stale_checks"], v["report"]))
check(all("evidence for different code" in c["basis"] for c in v["checks"]), "the basis of stale evidence has its fingerprint and the fingerprint now", v["checks"][0]["basis"])
record_all(repo, skip=("Independent Review Gate",))
check(core.verdict(repo)["verdict"] == "DONE", "recording evidence again for the edited code → DONE")
# case: E2verdict-05
repo, _ = prepare()
record_all(repo, skip=("Independent Review Gate", "Regression Test"))
record(repo, "Regression Test", "pass", "unknown")
v = core.verdict(repo)
check(v["verdict"] == "INCOMPLETE" and v["stale_checks"] == ["Regression Test"], "evidence whose fingerprint could not be measured (unknown) is evidence for no code — not DONE", v["message"])

# unknown · approval · plan mismatch
# case: E2verdict-06
repo, _ = prepare()
(repo / ".oqa" / "core.json").unlink()
v = core.verdict(repo)
check(v["verdict"] == "UNKNOWN" and v["exit_code"] == 2, "no core.json → UNKNOWN (2)", v["message"])
v = core.verdict(tempfile.mkdtemp())
check(v["verdict"] == "UNKNOWN" and v["exit_code"] == 2, "a folder that is not git · has no core.json → UNKNOWN", v["message"])
# case: E2verdict-07
repo = make_repo()
(repo / ".oqa" / "core.json").write_text("{broken", encoding="utf-8")
v = core.verdict(repo)
check(v["verdict"] == "UNKNOWN" and "error during verdict" in v["message"], "a broken core.json → UNKNOWN without raising", v["message"])
outside = Path(tempfile.mkdtemp())
(outside / ".oqa").mkdir()
(outside / ".oqa" / "core.json").write_text(json.dumps({"created": "2099-01-01T10:00:00", "checks": []}), encoding="utf-8")
v = core.verdict(outside)
check(v["verdict"] == "UNKNOWN" and "fingerprint" in v["message"], "the fingerprint now cannot be measured (not git) → UNKNOWN — not folded into DONE", v["message"])
# case: E2verdict-08
repo, _ = prepare(spec_text=BASE_SPEC.replace("\"yes, go ahead\"", "<user's words as they are>"))
record_all(repo, skip=("Independent Review Gate",))
v = core.verdict(repo)
check(v["verdict"] == "INCOMPLETE" and any("\"## Approval\" holds no quoted approval" in t and "placeholder" in t for t in v["reasons"]) and not any("is empty" in t for t in v["reasons"]),
      "the spec's \"## Approval\" holds only the placeholder → INCOMPLETE; the reason says it holds no quoted approval and names the placeholder (wording changed: it used to say the section \"is empty\")", v["reasons"])
# case: E2verdict-09
repo = make_repo()
core.plan(repo)
v = core.verdict(repo)
check(v["verdict"] == "NEEDS DECISION" and v["exit_code"] == 3 and v["checks"] == [] and any("no uncommitted change to verify" in t and "before committing" in t for t in v["to_confirm"]),
      "no changes · approval present → NEEDS DECISION (3), never DONE without checks — nothing uncommitted is left to verify, so plan and verdict must be run before committing (otherwise committing first gives DONE with zero evidence)", v["message"])

# Result of the verification report evidence — fail (incomplete n) · unknown (cannot judge) · equivalent
# case: E2verdict-10
repo, _ = prepare()
record_all(repo, skip=("Independent Review Gate", "Verification Report"))
v = core.verdict(repo)
check(v["verdict"] == "INCOMPLETE" and v["report"] == "no report", "evidence for the checks but none for the report → INCOMPLETE", v["message"])
record(repo, "Verification Report", "pass", "abcdef123456")
v = core.verdict(repo)
check(v["verdict"] == "INCOMPLETE" and v["report"] == "code changed after report" and any(t.startswith("code changed after report") for t in v["reasons"]) and not v["stale_checks"],
      "the checks' evidence is for the code now but the report evidence has an old fingerprint → INCOMPLETE (code changed after report)", (v["reasons"], v["report"]))
record(repo, "Verification Report", "fail")
v = core.verdict(repo)
check(v["verdict"] == "INCOMPLETE" and v["report"] == "incomplete" and any(t.startswith("verification report:") for t in v["reasons"]), "the report evidence is fail (incomplete n) → INCOMPLETE", (v["reasons"], v["report"]))
record(repo, "Verification Report", "unknown")
v = core.verdict(repo)
check(v["verdict"] == "NEEDS DECISION" and v["report"] == "cannot judge" and any(t.startswith("verification report:") for t in v["to_confirm"]), "the report evidence is unknown (cannot judge) → NEEDS DECISION", (v["verdict"], v["to_confirm"]))
record(repo, "Verification Report", "pass", equivalent=1)
v = core.verdict(repo)
check(v["verdict"] == "NEEDS DECISION" and any("left 1 equivalent mutant(s) out" in t for t in v["to_confirm"]), "the report left out 1 equivalent → NEEDS DECISION even when it passed (no self-granted exemption)", (v["verdict"], v["to_confirm"]))
record(repo, "Verification Report", "pass")
check(core.verdict(repo)["verdict"] == "DONE", "a passing report without equivalents → DONE")

# Independent review gate — approved · rejected recorded with the fingerprint when the review bundle was made
# case: E2verdict-11
repo, p = prepare(spec_text=GATE_SPEC)
check("Independent Review Gate" in check_names(p), "(setup) a spec with the gate on → the Independent Review Gate check", check_names(p))
record_all(repo, skip=("Independent Review Gate",))
v = core.verdict(repo)
check(v["verdict"] == "INCOMPLETE" and v["missing_checks"] == ["Independent Review Gate"] and v["review_gate"] == {"rounds": 0, "approved": False}, "gate on · no approval evidence → INCOMPLETE", v["message"])
record(repo, "Independent Review Gate", "rejected")
v = core.verdict(repo)
check(v["verdict"] == "INCOMPLETE" and v["failed_checks"] == ["Independent Review Gate"] and v["review_gate"] == {"rounds": 1, "approved": False}, "rejected evidence → INCOMPLETE (failed check) · round 1", v["message"])
record(repo, "Independent Review Gate", "approved")
v = core.verdict(repo)
check(v["verdict"] == "DONE" and v["review_gate"] == {"rounds": 2, "approved": True}, "the latest round is approved → DONE (covers the earlier rejected round — same fingerprint)", v["message"])
record(repo, "Independent Review Gate", "rejected")
check(core.verdict(repo)["verdict"] == "NEEDS DECISION", "rejected again after approval (round 3) → not approved, and 3 rounds without approval, so NEEDS DECISION")
# case: E2verdict-12
repo, _ = prepare(spec_text=GATE_SPEC)
record_all(repo, skip=("Independent Review Gate",))
record(repo, "Independent Review Gate", "approved", "abcdef123456")
v = core.verdict(repo)
check(v["verdict"] == "INCOMPLETE" and v["stale_checks"] == ["Independent Review Gate"] and v["review_gate"]["approved"] is False,
      "an approval given for old code (another fingerprint) is not an approval", (v["message"], v["review_gate"]))
for _n in range(2):
    record(repo, "Independent Review Gate", "rejected")
v = core.verdict(repo)
check(v["verdict"] == "NEEDS DECISION" and v["exit_code"] == 3 and v["to_ask"], "3 rounds of the gate without approval for the code now → NEEDS DECISION (3)", v["message"])

# E4 — evidence of another repository is not seen (one file per repository)
# case: E2verdict-13
repo, _ = prepare()
other = make_repo(name="other")
record_all(other)
v = core.verdict(repo)
check(v["verdict"] == "INCOMPLETE" and len(v["missing_checks"]) == 4 and len(_ev.read(other)) == 6 and _ev.read(repo) == [],
      "evidence recorded in another repository is not seen by this repository's verdict", v["missing_checks"])

# E5 — writing a verdict line · an approval table into the spec text by hand does not change the verdict
# case: E2verdict-14
repo, _ = prepare(spec_text=GATE_SPEC + "\n| round | date | gate | model | open | verdict | result |\n|---|---|---|---|---|---|---|\n| 1 | 11-11 | result | m | 0 · 0 · 0 | **approved** | r1 |\n")
spec_path = repo / SPEC_FILE
spec_path.write_text(spec_path.read_text(encoding="utf-8").replace("## Results\n", "## Results\n\n### Verification Report\n\ncounted by report.py · fingerprint %s\n\n**Verdict: all passed**\n\n" % fingerprint(repo), 1), encoding="utf-8")
v = core.verdict(repo)
check(v["verdict"] == "INCOMPLETE" and v["report"] == "no report" and "Independent Review Gate" in v["missing_checks"] and v["review_gate"]["approved"] is False,
      "\"**Verdict: all passed**\" · approval table rows in the spec text are not evidence (bold approval · copied verdict line · fake section)", (v["report"], v["missing_checks"]))

# check_evidence — lines with a name (individual tests) count by the latest result per name; one bad one means failed
# case: E2verdict-15
repo, _ = prepare()
record_all(repo, skip=("Independent Review Gate", "Regression Test"))
record(repo, "Regression Test", "fail", name="a_test")
record(repo, "Regression Test", "pass", name="b_test")
v = core.verdict(repo)
check(v["failed_checks"] == ["Regression Test"], "a_test fails, then another test b_test passes → still failed", v["message"])
record(repo, "Regression Test", "pass", name="a_test")
check(core.verdict(repo)["verdict"] == "DONE", "the same a_test run again and passing → DONE")
fp = fingerprint(repo)
lines = [{"kind": "a", "fingerprint": fp, "result": "pass", "time": "2099-01-01T10:00:00"}, {"kind": "a", "fingerprint": fp, "result": "fail", "time": "2099-01-01T10:05:00", "name": "x_test"}]
check(_ev.check_evidence(lines, "a", fp)[0] == "failed", "the failure of an individual test recorded after a whole-check pass line stays a failure", _ev.check_evidence(lines, "a", fp)[:2])
same_second = [dict(d, time="2099-01-01T10:00:00") for d in lines]
check(_ev.check_evidence(same_second, "a", fp)[0] == "failed" and _ev.check_evidence(list(reversed(same_second)), "a", fp)[0] == "ok",
      "which line is the later one is decided by the order the lines were written, not by their time (one-second resolution) — a whole-check pass followed by an individual fail in the same second is failed; written the other way round it is ok", _ev.check_evidence(same_second, "a", fp)[:2])
lines2 = [{"kind": "a", "fingerprint": fp, "result": "fail", "time": "2099-01-01T10:00:00", "name": "x_test"}, {"kind": "a", "fingerprint": fp, "result": "pass", "time": "2099-01-01T10:05:00"}]
check(_ev.check_evidence(lines2, "a", fp)[0] == "ok", "an individual test fails, then the whole check is run again and a pass line is recorded → ok (the report reruns everything)", _ev.check_evidence(lines2, "a", fp)[:2])
check(_ev.check_evidence([], "a", fp)[0] == "missing" and _ev.check_evidence(lines, "a", None)[0] == "stale" and _ev.check_evidence([{"kind": "a", "fingerprint": fp, "result": "unknown"}], "a", fp)[0] == "unknown",
      "no evidence lines → missing · fingerprint now unknown → stale · a recorded result of unknown → unknown: each gets its own state")
try:
    _ev.record(repo, "a", "end")
    check(False, "a result word that is not one of the fixed values is not accepted")
except ValueError:
    check(True, "a result word that is not one of the fixed values (\"end\") is not accepted — ValueError")
(repo / ".oqa" / "evidence.jsonl").write_text('broken line\n[1]\n{"kind": "a", "fingerprint": "x", "result": "pass"}\n', encoding="utf-8")
check(_ev.read(repo) == [{"kind": "a", "fingerprint": "x", "result": "pass"}], "broken lines · non-dict lines are skipped when reading")

# Manual checks — recorded only with the fingerprint measured at start (recording with the fingerprint at that moment would claim the code edited afterwards was looked at too)
# case: E2verdict-16
repo, _ = prepare()
at_start = fingerprint(repo)
r = subprocess.run([sys.executable, str(SCRIPTS / "evidence.py"), "record", "--repo", str(repo), "--kind", "Silent Failure Review", "--result", "done"],
                   capture_output=True, text=True, encoding="utf-8", errors="replace")
check(r.returncode == 2 and "--fingerprint" in r.stdout and _ev.read(repo) == [], "recording a review check without --fingerprint records nothing · exit code 2", r.stdout.strip()[:80])
write(repo, "tools/score.py", "def tally(x):\n    return x + 9\n")                 # edited after accepting a review finding
r = subprocess.run([sys.executable, str(SCRIPTS / "evidence.py"), "record", "--repo", str(repo), "--kind", "Silent Failure Review", "--result", "done",
                    "--fingerprint", at_start, "--basis", "findings 2 · accepted 2"], capture_output=True, text=True, encoding="utf-8", errors="replace")
core.plan(repo)
v = core.verdict(repo)
check(r.returncode == 0 and states(v).get("Silent Failure Review") == "stale", "edited after the review → that review is \"stale\" — it did not look at the edit", (r.stdout.strip()[:60], states(v)))
r = subprocess.run([sys.executable, str(SCRIPTS / "evidence.py"), "fingerprint", "--repo", str(repo)], capture_output=True, text=True, encoding="utf-8", errors="replace")
check(r.returncode == 0 and r.stdout.strip() == fingerprint(repo), "evidence.py fingerprint — prints the fingerprint now", r.stdout.strip())
r = subprocess.run([sys.executable, str(SCRIPTS / "evidence.py"), "show", "--repo", str(repo)], capture_output=True, text=True, encoding="utf-8", errors="replace")
check(r.returncode == 0 and "Silent Failure Review" in r.stdout and "stale" in r.stdout, "evidence.py show — the state of every check against the fingerprint now", r.stdout.strip()[-80:])

# Baseline — the evidence recorded by baseline.py (by kind only — the Regression evidence of baseline_test does not become Baseline)
# case: E2verdict-17
repo = make_repo(with_map=False)
write(repo, "README.md", "x\n")
core.plan(repo)
record(repo, "Regression Test", "pass", name="baseline_test")
v = core.verdict(repo)
check(states(v) == {"Regression Test": "ok", "Baseline": "missing"}, "only baseline_test ran → Baseline is missing", states(v))
record(repo, "Baseline", "pass")
check(states(core.verdict(repo)).get("Baseline") == "ok", "with Baseline evidence → ok")

# case: E2verdict-18
repo, _ = prepare()
record_all(repo, skip=("Independent Review Gate", "Mutation Test"))
r = subprocess.run([sys.executable, str(SCRIPTS / "core.py"), "verdict", "--repo", str(repo), "--json"], capture_output=True, text=True, encoding="utf-8", errors="replace")
try:
    j = json.loads(r.stdout)
except ValueError:
    j = {}
check(r.returncode == 1 and j.get("missing_checks") == ["Mutation Test"], "command-line verdict --json → INCOMPLETE · exit code 1", r.stdout[-120:])

# case: E1record-01
print("── recording: tools record evidence in that repository · with the fingerprint of the code that ran ──")
# (1) tests (_harness.py) — in the repository the test file is in, with that fingerprint only when the start · end fingerprints are equal
# case: E1record-02
repo = make_repo()
write(repo, "tests/pass_test.py", TEST_HEAD + "print('failures: 0')\nsys.exit(0)\n")
write(repo, "tests/fail_test.py", TEST_HEAD + "sys.exit(1)\n")
write(repo, "tests/bare_test.py", TEST_HEAD + "print('failures: 1')\nraise SystemExit(1)\n")
write(repo, "tests/edit_test.py", TEST_HEAD + "open(r'%s', 'a', encoding='utf-8').write('# edited while running\\n')\n" % (repo / "tools" / "score.py"))
env = dict(os.environ)
fp_before = fingerprint(repo)
for name in ("pass_test", "fail_test"):
    subprocess.run([sys.executable, str(repo / "tests" / (name + ".py"))], capture_output=True, env=env, cwd=tempfile.mkdtemp())
recorded = {d.get("name"): d for d in _ev.read(repo)}
check(recorded.get("pass_test", {}).get("result") == "pass" and recorded.get("fail_test", {}).get("result") == "fail" and all(d["kind"] == "Regression Test" and d["fingerprint"] == fp_before for d in recorded.values()),
      "when a test ends through sys.exit, pass (0) · fail (1) and the fingerprint of the code that ran go into that repository's evidence (whatever the working folder)", recorded)
subprocess.run([sys.executable, str(repo / "tests" / "bare_test.py")], capture_output=True, env=env, cwd=tempfile.mkdtemp())
core.plan(repo)
recorded = {d.get("name"): d for d in _ev.read(repo)}
v = core.verdict(repo)
check(recorded.get("bare_test", {}).get("result") == "unknown" and "sys.exit" in recorded.get("bare_test", {}).get("basis", "") and states(v).get("Regression Test") == "failed",
      "a test that ends with a bare `raise SystemExit(1)` told the harness no exit code → recorded as unknown, never pass (the exit code is unknown by default)", (recorded.get("bare_test"), states(v)))
record(repo, "Regression Test", "pass", name="fail_test")
v = core.verdict(repo)
check(states(v).get("Regression Test") == "unknown" and any("Regression Test — result unknown" in t and "bare_test" in t for t in v["to_confirm"]),
      "… and that unknown line is not covered by the passes of the other tests: the Regression check is unknown (to confirm), not ok", (states(v), v["to_confirm"]))
subprocess.run([sys.executable, str(repo / "tests" / "edit_test.py")], capture_output=True, env=env)
recorded = {d.get("name"): d for d in _ev.read(repo)}
check(recorded.get("edit_test", {}).get("fingerprint") == "unknown", "the code changes while the test runs → fingerprint \"unknown\" — which code the result belongs to is unknown", recorded.get("edit_test"))
copy = Path(tempfile.mkdtemp()) / "copy"
shutil.copytree(repo, copy, ignore=shutil.ignore_patterns(".git", ".oqa"))
subprocess.run([sys.executable, str(copy / "tests" / "pass_test.py")], capture_output=True, env=env)
check(not (copy / ".oqa" / "evidence.jsonl").exists(), "a test run in a copy outside git (a mutation copy) records no evidence")
# The end-of-test hook called directly — the evidence goes to the repository of the test file
# case: E1record-03
repo = make_repo()
write(repo, "tests/direct_test.py", "print(1)\n")
old_argv, old_start, old_code = sys.argv[0], _h._start_fingerprint, _h._end["code"]
sys.argv[0], _h._start_fingerprint = str(repo / "tests" / "direct_test.py"), fingerprint(repo)
try:
    _h._end["code"] = None                               # nobody has said how the test ended
    _h._test_end()
    _h._end["code"] = 0                                  # as sys.exit(0) · run_cases would set it
    _h._test_end()
finally:
    sys.argv[0], _h._start_fingerprint, _h._end["code"] = old_argv, old_start, old_code
recorded = _ev.read(repo)
check(len(recorded) == 2 and recorded[1]["name"].split(":")[0] == "direct_test" and recorded[1]["result"] == "pass" and recorded[1]["basis"] == "exit code 0" and recorded[1]["fingerprint"] == fingerprint(repo),
      "the end-of-test hook records the test evidence in that repository once the exit code is known to be 0 (nothing but the repository of the test file is needed)", recorded)
check(len(recorded) == 2 and recorded[0]["result"] == "unknown" and recorded[0]["basis"] == _h.NOT_KNOWN and recorded[0]["fingerprint"] == fingerprint(repo),
      "… and while the exit code is not known (None) the line says unknown, with the reason as basis — it used to be a pass by default", recorded)

# case: planside-01
print("── plan side — conditions for picking checks ──")
# case: planside-02
repo = make_repo()
write(repo, "screen/board.html", "<p>changed</p>\n")
check("Requirements Traceability" in check_names(core.plan(repo)), "only ui changed and the spec has E numbers → Traceability is picked")
# case: planside-03
repo = make_repo()
write(repo, "memo.md", "text only\n")
check("Requirements Traceability" not in check_names(core.plan(repo)), "only docs changed → Traceability is not picked")
# case: planside-04
repo = make_repo()
write(repo, "tools/score.py", "def tally(x):\n    return x + 2\n")
write(repo, "tests/group/repro_one.py", "print(1)\n")
write(repo, "tests/group/repro_two.py", "print(2)\n")
check("Reasoning Review" not in check_names(core.plan(repo)), "two new .py in a test folder are not a structural change (Reasoning Review stays off)")
# case: planside-05
repo = make_repo()
write(repo, "hooks/scripts/h.py", "import argparse\np = argparse.ArgumentParser()\na = p.parse_args()\nb = p.parse_args([])\n")
check("Fuzz Test (LLM)" not in check_names(core.plan(repo)), "a tool that only adds command-line boilerplate (argparse · parse_args) does not switch Fuzz on")
# case: planside-06
repo = make_repo()
write(repo, "hooks/scripts/h.py", "import argparse\nparser = argparse.ArgumentParser()\nparser.add_argument('--a')\nsub = parser.add_subparsers()\nargs = parser.parse_known_args()\n")
check("Fuzz Test (LLM)" not in check_names(core.plan(repo)), "parser · add_subparsers · parse_known_args are not counted as input parsing either")
# case: planside-07
repo = make_repo(spec_text=BASE_SPEC.replace("## Expected results", "## Tools\n- blind review (blind-check): not used — no money · permission judgement\n\n## Expected results"))
write(repo, "README.md", "x\n")
check("Blind Review" not in check_names(core.plan(repo)), "a \"Tools\" line that says the blind review is not used does not switch Blind on")

# case: finish-01
print("── finish line — the spec decides the verification that is required ──")
finish_spec = BASE_SPEC.replace("## Expected results", "## Finish line\n- Regression Test · Mutation Test\n- Requirements Traceability\n\n## Expected results")
repo, p = prepare(spec_text=finish_spec)
check({c["name"]: c["required"] for c in p["checks"]} == {"Regression Test": True, "Mutation Test": True, "Requirements Traceability": True, "Silent Failure Review": False}
      and p["finish_line"] == ["Mutation Test", "Regression Test", "Requirements Traceability"] and p["finish_line_unknown"] == [],
      "checks named in the spec's \"## Finish line\" are required, what the rule table picks beyond that (Silent) is recommended", [(c["name"], c["required"]) for c in p["checks"]])
record_all(repo, skip=("Independent Review Gate", "Silent Failure Review"))
v = core.verdict(repo)
check(v["verdict"] == "DONE" and states(v)["Silent Failure Review"] == "missing" and not v["missing_checks"] and "all 3 required check(s)" in v["message"],
      "a recommended check does not block the verdict without evidence (its state is still shown)", (v["message"], states(v)))
record(repo, "Silent Failure Review", "fail")
v = core.verdict(repo)
check(v["verdict"] == "INCOMPLETE" and v["failed_checks"] == ["Silent Failure Review"], "even a recommended check with failed evidence means INCOMPLETE (what went wrong is not overlooked)", v["message"])
repo, p = prepare()
check(all(c["required"] for c in p["checks"]) and p["finish_line"] is None, "without a finish line in the spec, every check the rule table picked is required as before", p["checks"])
# case: finish-02
repo = make_repo(spec_text=BASE_SPEC.replace("## Expected results", "## Finish line\n- Regression Test · Blind Review · NoSuchCheck\n\n## Expected results"))
write(repo, "README.md", "x\n")
p = core.plan(repo)
check({c["name"]: c["required"] for c in p["checks"]} == {"Regression Test": True, "Blind Review": True} and p["finish_line_unknown"] == ["NoSuchCheck"]
      and "Finish line" in next(c["reason"] for c in p["checks"] if c["name"] == "Blind Review"),
      "a check named in the finish line is required even when the rule table did not pick it · names not in the rule table are kept apart", (p["checks"], p["finish_line_unknown"]))
record(repo, "Regression Test")
record(repo, "Verification Report")
v = core.verdict(repo)
check(v["verdict"] == "INCOMPLETE" and v["missing_checks"] == ["Blind Review"] and any("NoSuchCheck" in t for t in v["to_confirm"]),
      "a finish-line check without evidence → INCOMPLETE · unknown names are raised as to confirm (a typo does not drop silently)", (v["message"], v["to_confirm"]))
record(repo, "Blind Review", "done")
check(core.verdict(repo)["verdict"] == "NEEDS DECISION", "all finish-line checks filled but an unknown name remains → NEEDS DECISION, not DONE")
repo, p = prepare(spec_text=GATE_SPEC.replace("## Expected results", "## Finish line\n- Regression Test\n\n## Expected results"))
required = {c["name"]: c["required"] for c in p["checks"]}
check(required.get("Independent Review Gate") is True and required.get("Mutation Test") is False and required.get("Regression Test") is True,
      "the independent review gate of a spec with the gate on is required even when the finish line does not name it (the user switched it on)", required)
record(repo, "Regression Test")
record(repo, "Verification Report")
v = core.verdict(repo)
check(v["verdict"] == "INCOMPLETE" and v["missing_checks"] == ["Independent Review Gate"], "… without that approval evidence → INCOMPLETE", v["message"])
check(core.read_finish_line("## Finish line\nonly prose and no list\n") == (set(), []) and core.read_finish_line("## Another section\n- Regression Test\n") == (None, [])
      and core.read_finish_line("## Finish line\n- `Regression Test`, Mutation Test\n* Fuzz Test (LLM)\n") == ({"Regression Test", "Mutation Test", "Fuzz Test (LLM)"}, []),
      "reading the finish line — list lines only · split on the middle dot and the comma · no section is None (no finish line), told apart from an empty finish line")
# case: finish-03
# the finish line changed after the plan · a finish line without a list
finish_one = BASE_SPEC.replace("## Expected results", "## Finish line\n- Regression Test\n\n## Expected results")
repo, p = prepare(spec_text=finish_one)
record(repo, "Regression Test")
record(repo, "Verification Report")
check(core.verdict(repo)["verdict"] == "DONE", "(setup) the finish line is Regression alone — DONE with that evidence")
write(repo, SPEC_FILE, finish_one.replace("- Regression Test\n", "- Regression Test · Mutation Test\n"))
v = core.verdict(repo)
check(v["verdict"] != "DONE" and any("Finish line" in t and "core.py plan" in t for t in v["reasons"]),
      "the spec's finish line changed after core plan (a check added) → not DONE by the old required checks — asks to plan again", (v["verdict"], v["message"]))
core.plan(repo)
v = core.verdict(repo)
check(v["verdict"] == "INCOMPLETE" and v["missing_checks"] == ["Mutation Test"], "… planning again demands evidence for the added check (Mutation)", v["message"])
repo, p = prepare(spec_text=BASE_SPEC.replace("## Expected results", "## Finish line\nthis work is looked at lightly (prose only, no list)\n\n## Expected results"))
check(p["finish_line"] == [] and all(c["required"] for c in p["checks"]) and len(p["checks"]) >= 3, "a finish-line section without a list of checks is not \"0 checks required\" — every picked check is required", p["checks"])
record_all(repo, skip=("Independent Review Gate",))
v = core.verdict(repo)
check(v["verdict"] == "NEEDS DECISION" and any("Finish line" in t and "no list of checks" in t for t in v["to_confirm"]), "… with all evidence filled it is NEEDS DECISION, not DONE (the finish line could not be read)", (v["verdict"], v["to_confirm"]))

# case: speed-01
# one git call takes a noticeable fraction of a second, so the number of calls is the time (a diff per changed code file adds up)
print("── speed: Core plan does not start git once per file ──")
repo = make_repo()
write(repo, "tools/score.py", "import re\n\n\ndef tally(x):\n    return len(re.split(r',', x)) + len(x.split())\n")
write(repo, "hooks/scripts/h.py", "X = 1\nY = 'a b'.split()\n")
write(repo, "tools/fresh.py", "import re\nZ = re.compile(r'x')\n")
called = []
_real_git = core._git
core._git = lambda r_, *args: (called.append(args[0]), _real_git(r_, *args))[1]
try:
    p = core.plan(repo)
finally:
    core._git = _real_git
single = {f["path"]: len(core.INPUT_PARSING.findall(core.added_text(repo, f))) for f in core.changed(repo) if f["path"].endswith(".py")}
batch = core.added_texts(repo, [f for f in core.changed(repo) if f["path"].endswith(".py")])
check(called.count("diff") <= 2 and len(called) <= 4, "three changed code files, yet one plan runs git diff at most twice (one list · one added lines) · git at most four times in all", called)
check({path: len(core.INPUT_PARSING.findall(text)) for path, text in batch.items()} == single and single == {"hooks/scripts/h.py": 1, "tools/fresh.py": 1, "tools/score.py": 2}
      and batch["hooks/scripts/h.py"] == core.added_text(repo, {"path": "hooks/scripts/h.py"}) and "return x + 1" not in batch["tools/score.py"] and "return len(re.split" in batch["tools/score.py"],
      "the added lines taken in one call equal those taken per file (edited file · new file — a new file is whole) · the removed line (return x + 1) is not in", (single, {path: text[:40] for path, text in batch.items()}))
check("Manual Mutation Test" in check_names(p) and "Fuzz Test (LLM)" not in check_names(p), "… the picked checks stay the same (regex · string splitting → manual mutation; fewer than three input-parsing hits in each file → no Fuzz)", check_names(p))
measured = []
_real_fingerprint = core.fingerprint
core.fingerprint = lambda r_: (measured.append(1), _real_fingerprint(r_))[1]
called = []
core._git = lambda r_, *args: (called.append(args[0]), _real_git(r_, *args))[1]
try:
    core.verdict(repo)
finally:
    core.fingerprint, core._git = _real_fingerprint, _real_git
check(len(measured) == 1 and len(called) <= 2, "one verdict measures the fingerprint only once · other git calls at most two (list of changes — one diff · one ls-files)", (len(measured), called))

# case: manualmut-01
print("── manual mutation as an official check (Manual Mutation Test) ──")
repo = make_repo()
write(repo, "tools/score.py", "import re\n\n\ndef tally(x):\n    return len(re.split(r'\\s+·\\s+', x))\n")
p = core.plan(repo)
check("Manual Mutation Test" in check_names(p) and "regex" in next(c["reason"] for c in p["checks"] if c["name"] == "Manual Mutation Test"),
      "regex · string splitting on added lines → the manual mutation check is picked (the mutation tool cannot change the inside of a regex)", p["checks"])
record_all(repo, skip=("Independent Review Gate",))
v = core.verdict(repo)
check(v["verdict"] == "INCOMPLETE" and "Manual Mutation Test" in v["missing_checks"], "no manual mutation evidence → INCOMPLETE", v["message"])
record(repo, "Manual Mutation Test", "pass", name="list_a")
record(repo, "Manual Mutation Test", "fail", name="list_b")
v = core.verdict(repo)
check(v["verdict"] == "INCOMPLETE" and "Manual Mutation Test" in v["failed_checks"], "one of two lists fails → failed (the other list's pass does not cover it)", v["message"])
record(repo, "Manual Mutation Test", "pass", name="list_b")
check("Manual Mutation Test" not in core.verdict(repo)["missing_checks"] + core.verdict(repo)["failed_checks"], "that list run again and passing lifts it")
repo, p = prepare()
check("Manual Mutation Test" not in check_names(p), "not picked for a small fix (one line · no regex)", check_names(p))

# case: findings-01
print("── findings ledger — an open high finding means not DONE ──")
import findings as _fd  # noqa: E402
repo, _ = prepare()
record_all(repo, skip=("Independent Review Gate",))
fp_before = fingerprint(repo)
line = _fd.add(repo, "Independent review gate 1", "high", "pass then fail in the same second is hidden", "evidence.py:142")
v = core.verdict(repo)
check(line["id"] == "F-1" and line["status"] == "open" and v["verdict"] == "INCOMPLETE" and any("open high findings 1: F-1" in t for t in v["reasons"]) and fingerprint(repo) == fp_before,
      "an open high finding → INCOMPLETE · adding a finding leaves the code fingerprint as it is (evidence does not go stale)", (v["message"], fingerprint(repo) == fp_before))
not_closed = []
for status, extra in (("accepted", {}), ("accepted", {"reason": "fixed"}), ("rejected", {}), ("deferred", {"repro_test": "x"}), ("closed", {"reason": "x"})):
    try:
        _fd.close(repo, "F-1", status, **extra)
    except ValueError:
        not_closed.append(status)
check(not_closed == ["accepted", "accepted", "rejected", "deferred", "closed"] and _fd.open_findings(repo, "high")[0]["id"] == "F-1",
      "a finding closes as accepted only with a repro test name and as rejected · deferred only with a reason — accepted with just the reason \"fixed\", rejected without a reason, deferred with only a test name and an unknown status are all refused", not_closed)
_fd.add(repo, "Silent review", "medium", "a small one")
_fd.close(repo, "F-1", "accepted", repro_test="core:E2verdict-14")
v = core.verdict(repo)
check(v["verdict"] == "DONE" and v["open_findings"] == {"high": [], "medium": ["F-2"], "low": []}, "closing the high finding lifts it · an open medium finding does not block, only its count is shown", (v["message"], v["open_findings"]))
_fd.add(repo, "Reasoning review", "high", "second")
_fd.close(repo, "F-3", "rejected", reason="raised because the original was not given")
_fd.add(repo, "Reasoning review", "high", "third")
_fd.close(repo, "F-4", "deferred", reason="moved to a later task")
check(core.verdict(repo)["verdict"] == "DONE" and [d["status"] for d in _fd.read(repo)] == ["accepted", "open", "rejected", "deferred"], "high findings closed as rejected (reason) · deferred (reason) do not block · ids continue (F-3 · F-4)")
for args, expected in ((("missing", "high", "x"), None), (("who", "very-high", "x"), None), (("", "high", "x"), None), (("who", "high", " "), None)):
    try:
        _fd.add(repo, *args) if args[0] != "missing" else _fd.close(repo, "F-99", "deferred", reason="x")
        check(False, "findings — wrong input is not accepted", args)
    except ValueError:
        pass
check(len(_fd.read(repo)) == 4, "an id that does not exist · an unknown severity · an empty who · an empty what are not accepted (no line is added)", len(_fd.read(repo)))
r = subprocess.run([sys.executable, str(SCRIPTS / "findings.py"), "show", "--repo", str(repo)], capture_output=True, text=True, encoding="utf-8", errors="replace")
check(r.returncode == 0 and "open 1 (high 0 · medium 1 · low 0)" in r.stdout and "accepted 1 · rejected 1 · deferred 1" in r.stdout, "findings.py show — counts per status · exit code 0 without an open high", r.stdout[:120])
r = subprocess.run([sys.executable, str(SCRIPTS / "findings.py"), "add", "--repo", str(repo), "--who", "manual", "--severity", "high", "--what", "from the command line"], capture_output=True, text=True, encoding="utf-8", errors="replace")
r2 = subprocess.run([sys.executable, str(SCRIPTS / "findings.py"), "close", "--repo", str(repo), "--id", "F-5", "--status", "accepted"], capture_output=True, text=True, encoding="utf-8", errors="replace")
r3 = subprocess.run([sys.executable, str(SCRIPTS / "findings.py"), "show", "--repo", str(repo)], capture_output=True, text=True, encoding="utf-8", errors="replace")
check(r.returncode == 0 and "F-5 open" in r.stdout and r2.returncode == 2 and "repro test" in r2.stdout and r3.returncode == 1, "findings.py command line — add · a close without its condition is exit code 2 · show is exit code 1 with an open high", (r.stdout, r2.stdout))
with open(repo / ".oqa" / "findings.jsonl", "a", encoding="utf-8") as _f:
    _f.write("broken line\n")
v = core.verdict(repo)
check(v["verdict"] != "DONE" and any("cannot read the findings ledger" in t for t in v["to_confirm"]) and v["open_findings"] is None, "a broken line in the findings ledger is not folded into \"no findings\" (to confirm)", (v["verdict"], v["to_confirm"]))

# case: findings-02
print("── findings are tied to the check that raised them · a code fingerprint per finding ──")
import findings as _fd  # noqa: E402,F811
repo, _ = prepare()
record_all(repo, skip=("Independent Review Gate",))
line = _fd.add(repo, "Silent review 1", "medium", "a small one", check="Silent Failure Review")
v = core.verdict(repo)
check(line["check"] == "Silent Failure Review" and line["fingerprint"] == fingerprint(repo) and v["verdict"] == "INCOMPLETE" and any("Silent Failure Review" in t and "F-1" in t for t in v["reasons"]),
      "an open finding tied to a check → INCOMPLETE even when not high (that review is not done just by being run) · the finding carries the code fingerprint of that time", (line, v["message"]))
_fd.add(repo, "seen in passing", "medium", "one without a check")
closed = _fd.close(repo, "F-1", "deferred", reason="moved to a later task")
v = core.verdict(repo)
check(closed["closed_fingerprint"] == fingerprint(repo) and v["verdict"] == "DONE" and v["open_findings"]["medium"] == ["F-2"], "closing that check's finding lifts it (the fingerprint at closing is recorded too) · an open medium finding without a check does not block, as before", (closed, v["message"]))
_fd.add(repo, "typo", "low", "check name spelled wrong", check="NoSuchCheck")
v = core.verdict(repo)
check(v["verdict"] != "DONE" and any("NoSuchCheck" in t and "F-3" in t for t in v["to_confirm"]), "a finding's check name that is not in the rule table → to confirm (a typo does not silently untie it)", (v["verdict"], v["to_confirm"]))
repo, _ = prepare()
record_all(repo, skip=("Independent Review Gate",))
write(repo, ".oqa/findings.jsonl", '{"id": "F-1", "time": "2099-01-02T09:00:00", "who": "old line", "severity": "medium", "what": "a line without the check · fingerprint fields", "where": "", "status": "open", "reason": "", "repro_test": "", "closed_time": ""}\n')
r = subprocess.run([sys.executable, str(SCRIPTS / "findings.py"), "add", "--repo", str(repo), "--who", "manual", "--severity", "low", "--what", "from the command line", "--check", "Regression Test"], capture_output=True, text=True, encoding="utf-8", errors="replace")
check(core.verdict(repo)["verdict"] == "INCOMPLETE" and [d.get("check", "") for d in _fd.read(repo)] == ["", "Regression Test"] and r.returncode == 0,
      "an old line without the check · fingerprint fields is read too (as no check) · command-line --check", (r.stdout, core.verdict(repo)["message"]))
