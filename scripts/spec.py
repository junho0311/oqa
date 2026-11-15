# -*- coding: utf-8 -*-
"""**The spec the tools work against — which file it is, whether the user approved it, and whether the independent review gate is on.**

Specs live under `<repo>/.oqa/specs/*.md`. `<repo>/.oqa/current` names the current one (one line, a file name relative to `.oqa/specs/`).
Without `.oqa/current` the spec is known only when the folder holds exactly one spec file — no file is picked by its name.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from evidence import section as _evidence_section        # one definition of where a section starts and ends


def read_text(path):
    """Text of a file — read as UTF-8 with replacement characters, so a file in another encoding gives garbled text instead of an exception. None when it cannot be read"""
    try:
        return path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return None


def _section(text, heading):
    """Body of the `## heading` section (up to the next `## `). Empty text when missing — the pattern is the single one in evidence.section"""
    return (_evidence_section(text, heading) or "").strip()


def _is_spec(path):
    """A spec file: a regular `.md` file that is not the template copied as is (`YYYY-MM-DD_name.md`) — a folder named `x.md` is not one"""
    return path.is_file() and path.suffix.lower() == ".md" and not path.name.startswith("YYYY")


def _named(repo):
    """(what `.oqa/current` says, the spec file it names | None). The text is "" when `.oqa/current` is missing or blank, and None when it is there but cannot be read"""
    repo = Path(repo)
    pointer, specs = repo / ".oqa" / "current", repo / ".oqa" / "specs"
    text = read_text(pointer)
    if text is None:
        return (None if pointer.exists() or pointer.is_symlink() else ""), None
    named = text.strip()
    if not named:
        return "", None
    try:
        p = (specs / named).resolve()
        # only a .md file directly inside the specs folder — a name such as `../notes.md` must not make some other file the spec
        if _is_spec(p) and p.parent == specs.resolve():
            return named, p
    except (OSError, ValueError):                        # a value that is not a path at all
        pass
    return named, None


def _candidates(repo):
    """The spec files directly inside `.oqa/specs`, sorted by name — what `.oqa/current` could name"""
    try:
        return sorted((f for f in (Path(repo) / ".oqa" / "specs").iterdir() if _is_spec(f)), key=lambda f: f.name)      # by the name as text — the same order on every system
    except OSError:
        return []


def current_spec(repo):
    """The file named in `.oqa/current`. When `.oqa/current` is missing or empty: the spec file in `.oqa/specs/` if there is exactly one; with
    several, None — no file is picked by its name (the last name in order made `draft.md` or `README.md` win over `2026-01-10_new.md`).
    None when there is none.
    A `.oqa/current` that says something but does not name an existing spec file directly inside `.oqa/specs` (a missing file, a typo, a `YYYY…` template,
    a file that is not `.md`, a folder, a path outside the folder, unreadable) is None too — switching silently to another file would judge the work
    against another spec and its approval. current_problem() gives the reason in every one of these cases"""
    repo = Path(repo)
    specs = repo / ".oqa" / "specs"
    if not specs.is_dir():
        return None
    named, path = _named(repo)
    if named != "":
        return path
    candidates = _candidates(repo)
    return candidates[0] if len(candidates) == 1 else None


def current_problem(repo):
    """One line saying why the current spec cannot be told: `.oqa/current` cannot be followed (naming its value), or it is missing or empty while
    `.oqa/specs` holds several spec files (listing them). None when the spec is known, when there is no spec file at all, or no specs folder"""
    repo = Path(repo)
    if not (repo / ".oqa" / "specs").is_dir():
        return None
    named, path = _named(repo)
    if named is None:
        return ".oqa/current is there but cannot be read, so the current spec is unknown — write the file name of the spec to use into it"
    if named and path is None:
        return (".oqa/current names \"%s\", which is not a spec file (.md) directly inside .oqa/specs (a missing file, a template, not a .md file or a path outside the folder)"
                " — correct it to name the spec to use" % " ".join(named.split())[:200])
    candidates = [] if named else _candidates(repo)
    if len(candidates) > 1:
        return (".oqa/current is missing or empty and .oqa/specs holds %d spec files (%s), so the current spec is unknown"
                " — write the file name of the spec to use into .oqa/current" % (len(candidates), ", ".join(f.name for f in candidates)[:300]))
    return None


def approved(spec_path):
    """Is there user wording wrapped in quotes ("…" · “…”, also over several lines) in the "## Approval" section — the template's placeholder line
    (the one whose quoted part is still `<user's words …>`) is left out. Only that placeholder marks the template line: a line that still has `<date>`
    but real quoted words counts, and so does wording that contains < ("go ahead without the <div>", "replace <date> and go").
    The section must hold only the user's approving words: a program cannot tell a quoted question ("may I go ahead?") from an approval"""
    return approval_problem(spec_path) is None


def approval_problem(spec_path):
    """None when the spec is approved (see approved); otherwise one line saying what the "## Approval" section lacks — the file cannot be read, there is
    no such section, the section is empty, only the template's placeholder line is there, or there is text but none of it in quotes"""
    lead = "the spec's \"## Approval\" holds no quoted approval — "
    text = read_text(spec_path)
    if text is None:
        return lead + "the spec file cannot be read"     # a spec that cannot be read has no approval anyone can point at — never taken as approved
    if _evidence_section(text, "Approval") is None:
        return lead + "the spec has no such section"
    lines = _section(text, "Approval").splitlines()
    body = "\n".join(line for line in lines if "<user's words" not in line)
    # quotes holding only blanks are not wording — there must be something the user said between them
    if any(quoted.strip() for pair in re.findall(r"\"([^\"]+)\"|“([^”]+)”", body, re.S) for quoted in pair):
        return None
    if not any(line.strip() for line in lines):
        return lead + "the section is empty"
    if not body.strip():
        return lead + "only the template's placeholder line is there (replace it with the date and the user's own words in double quotes)"
    return lead + "the user's own approving words must stand in double quotes"


# The key and the words on · off are read whatever their letter case (`On ("yes")` and `Independent Review Gate:` were read as off), and a trailing
# `<- …` hint (the template's line carries one) is not part of the answer. The bracket of `on (…)` ends at the first `)` after which only a hint
# follows, so quoted words inside a hint are not taken for the user's words and `on (…) off` matches nothing.
# In front of the key a list marker (`-` `*` `+` `1.` `1)`) may stand, and markdown emphasis (`*` `_` backtick) may wrap the key with or without
# its colon (`- **Independent review gate:** on (…)`) — such a line was not recognised, and a gate the user switched on was skipped
EMPHASIS = r"[*_`]*"
GATE_LINE = re.compile(r"^\s*(?:[-*+]|\d+[.)])?\s*" + EMPHASIS + r"\s*Independent review gate\s*" + EMPHASIS + r"\s*:\s*" + EMPHASIS + r"(?P<rest>.*)$", re.I)
ON_REST = re.compile(r"^on\s*\((?P<inside>(?:[^\"]|\"[^\"]*\")*?)\)\s*(?:<-.*)?$", re.I)      # inside: plain characters and whole quoted texts (a bracket or an arrow inside quotes does not end it)
OFF_REST = re.compile(r"^off\s*(?:<-.*)?$", re.I)
QUOTE = re.compile(r"\"([^\"]+)\"")
HINT_LINE = re.compile(r"^\s*(?:<-|<!--.*-->\s*$)")       # a line that is only a hint: it starts with `<-`, or is an HTML comment


def _gate(text):
    """(state, the quoted words of an "on", the line the state was read from) — see gate_state"""
    body = _evidence_section(text, "Independent review gate") or ""
    lines = [line for line in body.splitlines() if GATE_LINE.match(line)]
    if not lines:
        # content but no gate line (a bare `- on ("yes")`, prose) — the user wrote something about the gate that cannot be read: not "no gate"
        content = [line for line in body.splitlines() if line.strip() and not HINT_LINE.match(line)]
        return ("unclear", None, content[0]) if content else (None, None, None)
    rest = GATE_LINE.match(lines[-1]).group("rest").strip()
    if OFF_REST.match(rest):
        return "off", None, lines[-1]
    m = ON_REST.match(rest)
    q = QUOTE.search(m.group("inside")) if m else None
    words = q.group(1).strip() if q else ""
    # `<user's words>` copied from the template hint is a placeholder, not something the user said
    if words and not (words.startswith("<") and words.endswith(">")):
        return "on", words, lines[-1]
    return "unclear", None, lines[-1]


def gate_state(text):
    """What the **last** "Independent review gate:" line of the "## Independent review gate" section says: "on" (`on (… "user words" …)`) · "off" (a clean `off`) ·
    "unclear" (there is such a line but it is neither — `on` without quoted words, `on ()`, `maybe`, `on (…) off`, a negated on, quoted words that are
    a placeholder such as `"<user's words>"`; or the section has content but no such line at all — a bare `- on ("yes")`, prose) ·
    None (no such section, or a section without content: nothing but blank lines, HTML comments and `<-` hint lines).
    The last gate line wins: a later `off` line overrides an earlier `on`. Unclear is never on, and never silently off — the verdict asks about it"""
    return _gate(text)[0]


def gate_on_quote(text):
    """The user's quoted words when gate_state is "on"; otherwise None (off · unclear · no gate line)"""
    return _gate(text)[1]


def gate_problem(text):
    """One line that quotes the line in question when gate_state is "unclear"; otherwise None"""
    state, _, line = _gate(text)
    if state != "unclear":
        return None
    if not GATE_LINE.match(line):
        return ("the spec's \"## Independent review gate\" section has content but no line `Independent review gate: off` or `Independent review gate: on (… \"user words\")`,"
                " so it cannot be told whether the gate is on: %s" % line.strip()[:200])
    return "the spec's last \"Independent review gate:\" line is neither `off` nor `on (… \"user words\")`, so it cannot be told whether the gate is on: %s" % line.strip()[:200]
