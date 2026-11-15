# -*- coding: utf-8 -*-
"""**Findings ledger — one line per thing a review, gate or mutation run found; Core watches each until it is closed.**

    python findings.py add   --repo <root> --who "Independent review gate 1" --severity high --what "pass then fail in the same second is hidden" [--where evidence.py:142]
    python findings.py close --repo <root> --id F-3 --status accepted --repro-test "tests/test_login.py::test_lockout"        fixed, and a repro test exists
    python findings.py close --repo <root> --id F-4 --status rejected --reason "raised because the reviewer did not get the original — ..."
    python findings.py close --repo <root> --id F-5 --status deferred --reason "moved to a later task"
    python findings.py show  --repo <root>

Findings that live only in conversation and spec prose leave no structure behind — which ones were accepted and which test reproduces them — and Core
could only see "was the review run".
- Location: `<repo>/.oqa/findings.jsonl` — one line = one finding {id, time, who, severity (high · medium · low), what, where, check, fingerprint, status,
  reason, repro_test, closed_time, closed_fingerprint}. `.oqa` is excluded from the code fingerprint, so adding and closing findings does not make
  evidence stale. It is committed with the repository (it travels to other machines)
- Status: open -> accepted (fixed — closes only with a repro test name) · rejected (needs a reason) · deferred (needs a reason, e.g. a task id)
- Core verdict: **an open high finding means INCOMPLETE**. Open medium · low findings do not block the verdict; only their count is shown. But a finding
  **that names a check** (`--check "Silent Failure Review"`) keeps the verdict INCOMPLETE until it is closed, whatever its severity — the evidence of a
  review check is "was it run" + "are all findings of that review closed"
- fingerprint · closed_fingerprint: the code fingerprint at the moment the finding is added, and at the moment it is closed (which code it is raised
  against and which code it is closed on). Not used for the verdict
- A finding is not closed unless the closing condition is met (exit code 2) — so a high finding cannot be erased by a "rejected" with no reason
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

SEVERITIES = ("high", "medium", "low")
CLOSING_STATUSES = ("accepted", "rejected", "deferred")


def path(repo):
    return Path(repo) / ".oqa" / "findings.jsonl"


def read(repo):
    """Finding lines (in written order). No file -> empty list. A broken line is **not skipped, it stops the read** — if a line holding an open high
    finding broke and silently vanished, the verdict would become DONE"""
    try:
        text = path(repo).read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return []
    out = []
    for number, raw in enumerate(text.splitlines(), 1):
        if not raw.strip():
            continue
        try:
            entry = json.loads(raw)
        except ValueError:
            raise ValueError("cannot read findings ledger line %d: %s" % (number, path(repo)))
        if not isinstance(entry, dict) or not entry.get("id") or entry.get("severity") not in SEVERITIES or entry.get("status") not in ("open",) + CLOSING_STATUSES:
            raise ValueError("findings ledger line %d is malformed (id · severity · status): %s" % (number, path(repo)))
        out.append(entry)
    return out


def _write(repo, entries):
    path(repo).parent.mkdir(parents=True, exist_ok=True)
    path(repo).write_text("".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in entries), encoding="utf-8")


def _fingerprint(repo):
    """Current code fingerprint — records which code a finding was raised against and which code it was closed on. "unknown" when it cannot be taken"""
    try:
        from evidence import fingerprint
        return fingerprint(repo) or "unknown"
    except Exception:                                        # noqa: BLE001 — the fingerprint is an extra note. The finding is recorded even without it
        return "unknown"


def add(repo, who, severity, what, where="", check=""):
    """Record one finding as "open" and return its line. Ids continue from F-1.
    check — the name of the verification check that raised the finding (a name from the core.py rules table, e.g. "Silent Failure Review"). When given,
    Core keeps the verdict INCOMPLETE until the finding is closed (whatever the severity — a review is not finished just by being run). Whether the
    name is valid is checked by Core (calling core from here would make a circular import)"""
    if severity not in SEVERITIES:
        raise ValueError("severity must be one of %s: %r" % (" · ".join(SEVERITIES), severity))
    if not str(who).strip() or not str(what).strip():
        raise ValueError("who · what is empty")
    entries = read(repo)
    finding_id = "F-%d" % (max([int(entry["id"].split("-")[1]) for entry in entries] or [0]) + 1)
    line = {"id": finding_id, "time": time.strftime("%Y-%m-%dT%H:%M:%S"), "who": str(who).strip(), "severity": severity, "what": str(what).strip(), "where": str(where or "").strip(),
            "check": str(check or "").strip(), "fingerprint": _fingerprint(repo), "status": "open", "reason": "", "repro_test": "", "closed_time": "", "closed_fingerprint": ""}
    _write(repo, entries + [line])
    return line


def close(repo, finding_id, status, reason="", repro_test=""):
    """Close a finding — accepted needs a repro test name, rejected · deferred need a reason. ValueError (with the reason text) when it cannot be closed"""
    if status not in CLOSING_STATUSES:
        raise ValueError("status must be one of %s: %r" % (" · ".join(CLOSING_STATUSES), status))
    if status == "accepted" and not str(repro_test).strip():
        raise ValueError("\"accepted\" closes only with a repro test name (--repro-test) — saying it was fixed is not enough")
    if status in ("rejected", "deferred") and not str(reason).strip():
        raise ValueError("\"%s\" closes only with a reason (--reason)" % status)
    entries = read(repo)
    matching = [entry for entry in entries if entry["id"] == finding_id]
    if not matching:
        raise ValueError("no such id: %s" % finding_id)
    matching[0].update(status=status, reason=str(reason or "").strip(), repro_test=str(repro_test or "").strip(), closed_time=time.strftime("%Y-%m-%dT%H:%M:%S"), closed_fingerprint=_fingerprint(repo))
    _write(repo, entries)
    return matching[0]


def open_findings(repo, severity=None):
    """Open findings (only that severity when one is given) — the Core verdict looks at "open high" ones"""
    return [entry for entry in read(repo) if entry["status"] == "open" and (severity is None or entry["severity"] == severity)]


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    parser = argparse.ArgumentParser(description="Findings ledger — add · close · show")
    parser.add_argument("action", choices=("add", "close", "show"))
    parser.add_argument("--repo", required=True)
    parser.add_argument("--who", default="")
    parser.add_argument("--severity", default="")
    parser.add_argument("--what", default="")
    parser.add_argument("--where", default="")
    parser.add_argument("--check", default="", help="name of the verification check that raised the finding (core.py rules table) — when given, Core stays INCOMPLETE until it is closed")
    parser.add_argument("--id", default="")
    parser.add_argument("--status", default="")
    parser.add_argument("--reason", default="")
    parser.add_argument("--repro-test", default="")
    args = parser.parse_args(argv)
    repo = Path(args.repo).resolve()
    try:
        if args.action == "add":
            line = add(repo, args.who, args.severity, args.what, args.where, args.check)
            print("%s open · %s · %s — %s" % (line["id"], line["severity"], line["who"], line["what"]))
            return 0
        if args.action == "close":
            line = close(repo, args.id, args.status, args.reason, args.repro_test)
            print("%s %s · %s" % (line["id"], line["status"], line["repro_test"] or line["reason"]))
            return 0
        entries = read(repo)
    except ValueError as error:
        print(str(error))
        return 2
    counts = {status: sum(1 for entry in entries if entry["status"] == status) for status in ("open",) + CLOSING_STATUSES}
    print("findings %d — open %d (high %d · medium %d · low %d) · accepted %d · rejected %d · deferred %d · %s" % (
        len(entries), counts["open"], *[len(open_findings(repo, severity)) for severity in SEVERITIES], counts["accepted"], counts["rejected"], counts["deferred"], path(repo)))
    for entry in entries:
        print("  %-5s %-8s %-6s  %-22s %s%s" % (entry["id"], entry["status"], entry["severity"], entry["who"][:22], entry["what"][:70], (" <- " + (entry["repro_test"] or entry["reason"])[:50]) if entry["status"] != "open" else ""))
    return 1 if open_findings(repo, "high") else 0


if __name__ == "__main__":
    sys.exit(main())
