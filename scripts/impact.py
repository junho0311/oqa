# -*- coding: utf-8 -*-
"""**Record which test executes which line, and select only the tests that pass through changed lines.**

    python impact.py record --repo <root>                     run the test list and build the line map (.oqa/impact-map.json)
    python impact.py select --repo <root> [--range working] [--run] [--json result.json]

Why: running the whole suite after every edit is slow, and picking tests by feel misses things. An edit whose changed
lines no test passes through (one of two duplicated places fixed, a fix without a test) went unnoticed. Only the
**Python standard library** is used, so nothing has to be installed: while recording, a folder holding a
`sitecustomize.py` is put at the front of PYTHONPATH, so **every Python process** the test starts (including tests
that launch other scripts) reads it at startup and records the executed lines of files inside the repository with
`sys.settrace`. On Python 3.12 or later the same lines are recorded with `sys.monitoring` (each line only the first
time — fast on big repositories). With `OQA_TRACE_SETTRACE=1` settrace is used on 3.12+ as well.

The test list is `<repo>/.oqa/tests.json` — `[{"name": "calc", "command": "python tests/calc_test.py", "fail_pattern": "<optional — failure pattern for this test only>"}, ...]` (commands run from the repository root).

Not seen: processes started with `python -I` / `-E` / `-S` (they do not read sitecustomize), processes whose
environment was rebuilt and lost PYTHONPATH, tests that copy code to a temporary folder outside the repository and run
it there (not a file inside the repository, so not recorded), anything outside Python (JS, SQL files, documents),
code created at run time (exec).
The map is relative to HEAD at recording time — if HEAD moved, or it was recorded with modified .py files, or the range
is not "working", the map is reported as stale; it is still used, with a request to record again.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

RECORDER = r'''
import atexit, json, os, sys, threading
_root = os.path.normcase(os.path.abspath(os.environ.get("OQA_TRACE_ROOT", "")))
_out = os.environ.get("OQA_TRACE_OUT")
if _root and _out:
    _seen = {}
    _inside_cache = {}
    def _inside(filename):
        known = _inside_cache.get(filename)
        if known is None:
            # "<frozen importlib._bootstrap>" and "<string>" are not files - abspath would join them to the current folder (the repository) and record them as repository files
            full = os.path.normcase(os.path.abspath(filename)) if filename and not filename.startswith("<") else ""
            known = _inside_cache[filename] = bool(full) and full.startswith(_root + os.sep) and ("site-packages" not in full)
        return known
    def _on_line(frame, event, arg):
        if event == "line" and _inside(frame.f_code.co_filename):
            _seen.setdefault(frame.f_code.co_filename, set()).add(frame.f_lineno)
        return _on_line
    def _on_call(frame, event, arg):
        if event == "call" and _inside(frame.f_code.co_filename):
            _seen.setdefault(frame.f_code.co_filename, set()).add(frame.f_lineno)
            return _on_line
        return None
    # Python 3.12+ uses sys.monitoring - each line is reported only the first time and then switched off at that location (DISABLE). settrace is called
    # every time a line runs, which makes tests of a big repository several times slower. This recorder runs first, so taking the coverage (1), debugger (0)
    # or profiler (2) id would leave that tool inside the test without an id - use the free ids 3 and 4, and fall back to settrace when both are taken
    _tool = None
    _m = getattr(sys, "monitoring", None)
    if _m is not None and os.environ.get("OQA_TRACE_SETTRACE") != "1":
        for _tool_id in (4, 3):
            if _m.get_tool(_tool_id) is None:
                _m.use_tool_id(_tool_id, "oqa-impact")
                _tool = _tool_id
                break
    if _tool is not None:
        def _on_line_m(code, lineno):
            if _inside(code.co_filename):
                _seen.setdefault(code.co_filename, set()).add(lineno)
            return _m.DISABLE
        def _on_start_m(code, offset):
            # same as the settrace call event - when a function is called its header line (co_firstlineno) also counts as executed
            if code.co_name != "<module>" and _inside(code.co_filename):     # a module start is line 0 under settrace - not a line
                _seen.setdefault(code.co_filename, set()).add(code.co_firstlineno)
            return _m.DISABLE
        _m.register_callback(_tool, _m.events.LINE, _on_line_m)
        _m.register_callback(_tool, _m.events.PY_START, _on_start_m)
        _m.set_events(_tool, _m.events.LINE | _m.events.PY_START)
    def _dump():
        # stop recording first, then take a copy - otherwise _seen grows while it is being walked and raises RuntimeError
        sys.settrace(None)
        threading.settrace(None)
        if _tool is not None:
            _m.set_events(_tool, 0)
        data = {}
        for path, lines in list(_seen.items()):
            try:
                lines = sorted(n for n in lines if n and n > 0)          # line 0 (module start, 3.12+) and None are not code lines
                if lines:
                    data[os.path.relpath(path, _root).replace(os.sep, "/")] = lines
            except ValueError:
                continue
        try:
            with open(_out, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(data, ensure_ascii=False) + "\n")
        except OSError as error:
            sys.stderr.write("[impact] could not write the record: %s\n" % error)
    atexit.register(_dump)
    if _tool is None:
        sys.settrace(_on_call)
        threading.settrace(_on_call)
'''


def test_list(repo):
    path = repo / ".oqa" / "tests.json"
    if not path.is_file():
        raise SystemExit("no test list: %s — create it as [{\"name\": ..., \"command\": ...}]" % path)
    try:
        entries = json.loads(path.read_text(encoding="utf-8-sig"))
    except ValueError as error:                          # not JSON (a file cut off while being written) or not UTF-8 — a message, not a traceback
        raise SystemExit("malformed test list: %s — it cannot be read as UTF-8 JSON (%s)" % (path, error))
    if not isinstance(entries, list) or not all(isinstance(t, dict) and t.get("name") and t.get("command") for t in entries):
        raise SystemExit("malformed test list: %s" % path)
    names = [t["name"] for t in entries]
    twice = sorted({str(name) for name in names if names.count(name) > 1})
    if twice:
        # every table of results is kept by name: of two entries with one name only one was run and its result stood for both — a copied entry
        # that kept the name (a new failing test file) was reported as passed
        raise SystemExit("malformed test list: %s — the name %s is used by more than one entry; give every entry its own name" % (path, ", ".join(twice)))
    return [e for t in entries for e in (_expand_cases(t, repo) if t.get("cases") else [t])]


_case_cache = {}


def _expand_cases(test, repo):
    """Turn a test with "cases": true into one test per case — for each name printed by `command --list-cases`:
    {name: "test:case", command: "command --case case"}. Map recording, impact selection and mutant test selection then
    work per case. Stops when the list cannot be obtained — so the test does not silently vanish by expanding to 0 cases"""
    key = (str(repo), test["command"])
    if key not in _case_cache:
        code, output = run(test["command"] + " --list-cases", repo, timeout=120)
        lines = [ln.strip() for ln in (output or "").splitlines() if ln.strip()]
        names = lines[1:] if lines[:1] == ["# case list"] else []      # no header line means the test does not support --list-cases, so its ordinary output is not read as case names
        if code != 0 or not names:
            raise SystemExit("could not get the case list (%s --list-cases · exit code %s): %s" % (test["command"], code, (output or "")[-200:]))
        _case_cache[key] = names
    # case_base and case_name — the mutation tool uses them to run several cases of one mutant in a single program (`base --case a,b`)
    return [dict({k: v for k, v in test.items() if k != "cases"}, name="%s:%s" % (test["name"], case), command="%s --case %s" % (test["command"], case), case_base=test["command"], case_name=case)
            for case in _case_cache[key]]


def head(repo):
    done = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True)
    return done.stdout.strip() if done.returncode == 0 else None


def decode(data):
    """Child output — read as UTF-8, otherwise in this system's legacy encoding (on Windows a Python running without
    PYTHONUTF8 prints in the local code page). When the legacy reading gives fewer ASCII characters than the output has
    ASCII bytes, UTF-8 with replacement characters is used instead, so that no ASCII text of the output goes missing.

    The price of that rule, kept on purpose: in a double-byte code page whose second byte may be an ASCII value
    (cp932, the extended part of cp949, gbk) valid legacy text that holds such a character is read the UTF-8 way too —
    its wide characters come out as replacement characters, its ASCII text (the failure words the tools look for) is
    whole. The two cases cannot be told apart from the bytes: a stray byte followed by `F` and a real two-byte character
    ending in `F` are the same two bytes, so reading them as the character would lose the `F` of `FAILED`"""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        import locale
        try:
            text = data.decode(locale.getpreferredencoding(False) or "utf-8", "replace")
        except LookupError:
            return data.decode("utf-8", "replace")
        # In a double-byte code page a stray byte takes the ASCII letter after it as its second half, so a failure
        # line such as FAILED can lose a letter and be missed. Every ASCII byte must come out as an ASCII character;
        # when one was lost, the output is not in the legacy encoding either — keep every ASCII byte instead
        if sum(1 for ch in text if ch < "\x80") < sum(1 for byte in data if byte < 0x80):
            return data.decode("utf-8", "replace")
        return text


class _Job:
    """The started process and every process started below it as one unit — a Windows job object. Ending it also ends
    **grandchildren whose parent has already exited**.
    Killing the process tree (taskkill /T) only follows the parent-child links that exist at that moment, so it misses a
    git that has just started or a grandchild whose parent exited first — such leftover processes keep the copy folder
    locked so the mutation tool cannot delete it, and a grandchild holding the output pipe keeps the waiting side
    blocked until the time limit.
    When the job cannot be created (other OS, permissions) the handle is None — then only the process tree is ended, as
    before. The handle is used only inside the lock and closed only once (a closed number may be handed to another job)"""

    def __init__(self, proc):
        import threading
        self.proc, self.lock, self.handle = proc, threading.Lock(), None
        # Elsewhere the unit is the process group: the command is started in its own session, so its group id is its pid,
        # and the group outlives the started process as long as a member is alive
        self.group = proc.pid if os.name != "nt" else None
        if os.name != "nt":
            return
        try:
            import ctypes
            kernel = ctypes.windll.kernel32
            kernel.CreateJobObjectW.restype = ctypes.c_void_p
            kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
            kernel.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
            kernel.TerminateJobObject.argtypes = [ctypes.c_void_p, ctypes.c_uint]
            kernel.CloseHandle.argtypes = [ctypes.c_void_p]
            handle = kernel.CreateJobObjectW(None, None)
            if handle and kernel.AssignProcessToJobObject(handle, int(proc._handle)):
                self.handle, self.kernel = handle, kernel
            elif handle:
                kernel.CloseHandle(handle)
        except Exception:                                    # noqa: BLE001 — the job is a bonus. Tests still run without it
            self.handle = None

    def _kill_group(self):
        """End what is left of the process group (not Windows). No such group = nothing left"""
        if self.group is None:
            return
        import signal
        try:
            os.killpg(self.group, signal.SIGKILL)
        except OSError:
            pass

    def terminate(self):
        """End every process in the job (may be called more than once). Without a job, only the process tree"""
        with self.lock:
            if self.handle:
                self.kernel.TerminateJobObject(self.handle, 1)
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(self.proc.pid)], capture_output=True)
        else:
            self._kill_group()

    def reap_when_done(self):
        """When the started process exits by itself, end the leftover processes (grandchildren) — a leftover grandchild
        holding the output pipe keeps the reader from seeing the end. Waits in the background"""
        import threading

        def wait():
            self.proc.wait()
            with self.lock:
                if self.handle:
                    self.kernel.TerminateJobObject(self.handle, 1)
                else:
                    self._kill_group()
        if self.handle or self.group is not None:
            threading.Thread(target=wait, daemon=True).start()

    def close(self):
        with self.lock:
            if self.handle:
                self.kernel.TerminateJobObject(self.handle, 1)
                self.kernel.CloseHandle(self.handle)
                self.handle = None
            else:
                self._kill_group()


ENV_WORD = re.compile(r"""^([A-Za-z_][A-Za-z0-9_]*)=((?!["'])\S*)\s+(?=\S)""")


def split_env(command, cwd, env):
    """(command without its leading `NAME=value` words, env with those values set).

    A test command may start with environment settings, written the same way on every system:
    `PYTHONPATH={root}/src python -m pytest tests/test_x.py -q`. `{root}` becomes the folder the command runs in —
    the repository, or the throw-away copy a mutant lives in. That matters for a `src/` layout: an installed copy of the
    package (or an editable install, which points at the original folder) would otherwise be imported instead of the
    mutated code, and every mutant would look as if no test noticed it.
    PYTHONPATH is put in front of the value already there (the line recorder adds its own entry while recording);
    any other name is simply set. An empty PYTHONPATH word (`PYTHONPATH= python x.py`) leaves the value already there
    unchanged — an empty entry in front of it would mean "the current folder".
    `{root}` is replaced only inside those leading words, not elsewhere in the command (the command itself can use the
    OQA_ROOT environment variable).
    A value cannot contain spaces, and a value that starts with a quote character (`FOO="a b" python x.py`) is not
    taken as an environment word at all: from that word on the command is left exactly as it is, for the shell to handle."""
    env = dict(env)
    root = str(Path(cwd).resolve())
    while True:
        found = ENV_WORD.match(command)
        if not found:
            return command, env
        name, value = found.group(1), found.group(2).replace("{root}", root)
        if name == "PYTHONPATH" and env.get("PYTHONPATH"):
            value = value + os.pathsep + env["PYTHONPATH"] if value else env["PYTHONPATH"]
        env[name] = value
        command = command[found.end():]


def run_streaming(command, cwd, timeout, env, stop_pattern):
    """(exit code | None (timeout), stdout bytes, stderr bytes, line that matched stop_pattern | None) — reads the
    output line by line and **ends the process group on the spot** when stop_pattern (a line meaning failure) appears
    (for mutation one failure line means killed, so there is no reason to run the remaining cases).
    PYTHONUNBUFFERED is switched on so lines arrive at once. stop_pattern is checked against a single line (a pattern
    spanning several lines is not seen — then the command runs to the end and the caller checks the whole output)"""
    import threading
    extra = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
    env = dict(env if env is not None else os.environ, OQA_ROOT=str(Path(cwd).resolve()), PYTHONUNBUFFERED="1")
    command, env = split_env(command, cwd, env)
    proc = subprocess.Popen(command, shell=True, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, **extra)
    job = _Job(proc)
    chunks, hits = {"out": [], "err": []}, []
    pattern = re.compile(stop_pattern)

    def read(pipe, name):
        for line in iter(pipe.readline, b""):
            chunks[name].append(line)
            if not hits and pattern.search(decode(line)):
                hits.append(decode(line).strip()[:160])
                job.terminate()
        pipe.close()
    threads = [threading.Thread(target=read, args=(proc.stdout, "out"), daemon=True), threading.Thread(target=read, args=(proc.stderr, "err"), daemon=True)]
    for thread in threads:
        thread.start()
    timed_out = False
    try:
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            job.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
    finally:
        job.close()                                          # end leftover processes even after a clean exit — the output pipes close and the reader threads finish
    for thread in threads:
        thread.join(timeout=10)
    return (None if timed_out and not hits else proc.returncode), b"".join(chunks["out"]), b"".join(chunks["err"]), (hits[0] if hits else None)


def run_command(command, cwd, timeout, env):
    """(exit code | None (timeout), stdout bytes, stderr bytes). On timeout the **whole process group** is ended.
    subprocess.run(shell=True, timeout=...) only ends the shell when the time is up; the Python below it stays alive,
    holds the output pipe and blocks the waiting side forever (a single infinite-loop mutant would keep the report from
    ever finishing). The mutation, impact and trace tools all use this one function"""
    extra = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
    # A command can start with `NAME=value` words, and `{root}` in them stands for the folder the command runs in (see split_env).
    # OQA_ROOT — that same folder, the original repository or a temporary copy — is also exported for commands that want it
    env = dict(env if env is not None else os.environ, OQA_ROOT=str(Path(cwd).resolve()))
    command, env = split_env(command, cwd, env)
    proc = subprocess.Popen(command, shell=True, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, **extra)
    job = _Job(proc)
    job.reap_when_done()                                     # if a leftover grandchild holds the output pipe after the started process exits, the communicate below waits until the time limit
    try:
        out, err = proc.communicate(timeout=timeout)
        return proc.returncode, out or b"", err or b""
    except subprocess.TimeoutExpired:
        job.terminate()
        try:
            out, err = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, err = b"", b""
        return None, out or b"", err or b""
    finally:
        job.close()


def run(command, repo, extra_env=None, timeout=600):
    code, out, err = run_command(command, repo, timeout, {**os.environ, **(extra_env or {})})
    if code is None:
        return None, "timeout"
    return code, decode(out) + decode(err)


def record(repo, fail_pattern):
    tests = test_list(repo)
    work = Path(tempfile.mkdtemp(prefix="oqa_impact_"))
    (work / "sitecustomize.py").write_text(RECORDER, encoding="utf-8")
    # recording with modified tracked .py files makes the recorded line numbers disagree with HEAD — select reports such a map as stale (new files are matched as whole files, so they do not matter)
    modified = modified_py(repo)
    map_ = {"created": time.strftime("%Y-%m-%d %H:%M"), "HEAD": head(repo), "modified": modified, "tests": {}, "results": {}, "commands": {}}
    if modified:
        print("warning: recording with %d modified .py file(s) (%s) — the line numbers of those files disagree with HEAD, so this map is treated as stale. Record again after committing" % (len(modified), ", ".join(modified[:4])))
    try:
        _record_run(tests, work, repo, fail_pattern, map_)
    finally:
        shutil.rmtree(work, ignore_errors=True)        # recorder and jsonl folder — left behind they pile up in the temp folder
    (repo / ".oqa").mkdir(exist_ok=True)
    (repo / ".oqa" / "impact-map.json").write_text(json.dumps(map_, ensure_ascii=False), encoding="utf-8")
    print("map →", repo / ".oqa" / "impact-map.json")
    failed = sorted(name for name, result in map_["results"].items() if result != "pass")
    if failed:
        print("%d test(s) failed while recording: %s — the map is written, but their lines are recorded from a failing run (a test that stops early does not pass through the lines after that point). Fix them and record again"
              % (len(failed), ", ".join(failed)))
        return 1
    return 0


def _record_run(tests, work, repo, fail_pattern, map_):
    for test in tests:
        out_path = work / ("%s.jsonl" % re.sub(r"[^\w.-]", "_", test["name"]))
        extra = {"PYTHONPATH": str(work) + os.pathsep + os.environ.get("PYTHONPATH", ""),
                 "OQA_TRACE_ROOT": str(repo), "OQA_TRACE_OUT": str(out_path), "PYTHONDONTWRITEBYTECODE": "1"}
        started = time.time()
        code, output = run(test["command"], repo, extra)
        lines = {}
        if out_path.is_file():
            for row in out_path.read_text(encoding="utf-8").splitlines():
                for file, numbers in json.loads(row).items():
                    lines.setdefault(file, set()).update(numbers)
        map_["tests"][test["name"]] = {file: sorted(numbers) for file, numbers in lines.items()}
        failed = code is None or code != 0 or bool(re.search(test.get("fail_pattern") or fail_pattern, output))
        map_["results"][test["name"]] = "fail" if failed else "pass"
        map_["commands"][test["name"]] = test["command"]    # the command at recording time — the trace tool checks whether this map still belongs to that test
        print("%-14s %-4s %5.1fs · files %d · lines %d" % (test["name"], map_["results"][test["name"]], time.time() - started, len(lines), sum(len(numbers) for numbers in lines.values())))


# the output is parsed: no colour codes, no external diff program, no text conversion, and the file lines always `--- a/<path>` · `+++ b/<path>`
# (not `c/` · `w/` or no prefix at all) — whatever the user's git settings say (color.ui · diff.external · diff.noPrefix · diff.mnemonicPrefix)
# `--no-renames`: a renamed file is a deleted file and a new one, as core.py's list of changes has it. With rename detection the lines of the old
# path were looked up under the new one — a renamed and edited test file stayed a gap for good, and a renamed source file was not seen at all
DIFF_OPTIONS = ["--no-color", "--no-ext-diff", "--no-textconv", "--no-renames", "--src-prefix=a/", "--dst-prefix=b/"]


class GitFailed(SystemExit):
    """A git command whose output was needed did not succeed — its (empty) output must not be read as "nothing changed".
    A SystemExit, so a caller that does not handle it still stops with the message"""


def git_text(repo, args):
    """Output of `git <args>` in the repository. Raises GitFailed (naming the command) when git cannot be started or ends with an error"""
    shown = "git " + " ".join(args)
    try:
        done = subprocess.run(["git", "-C", str(repo), "-c", "core.quotepath=false"] + list(args), capture_output=True, text=True, encoding="utf-8", errors="replace")
    except OSError as error:
        raise GitFailed("`%s` failed: git could not be started (%s)" % (shown, error))
    if done.returncode != 0:
        raise GitFailed("`%s` failed (exit code %s): %s" % (shown, done.returncode, (done.stderr or "").strip()[-300:]))
    return done.stdout or ""


def changed_old_lines(repo, range_):
    """({file: set of old (HEAD side) line numbers}, {file: [number of added lines ...]}, {file: [[first new-side line, last line] ...]}) —
    the map is relative to HEAD, so matching uses old-side numbers. A pure addition uses the neighbouring lines only to
    select tests; the added lines themselves are not in the map, so they are returned separately (the third value is the
    current line range of that hunk — the report tool confirms it by "was a mutant on those lines killed"). A new file is None (whole file).
    Raises GitFailed when git cannot give the diff or the list of untracked files — an empty answer would read as "nothing changed" — and when
    a file line of the diff is not `--- a/<path>` · `+++ b/<path>` · `/dev/null`: a path read with a wrong prefix is a file no test knows"""
    diff = git_text(repo, ["diff"] + DIFF_OPTIONS + ["-U0"] + (["HEAD"] if range_ == "working" else [range_]))
    found, added, ranges, current, old, in_header = {}, {}, {}, None, None, False

    def side(row, prefix):
        """The path of a `--- ` / `+++ ` file line — None for /dev/null (that side does not exist)"""
        name = row[4:].rstrip("\t")                      # git puts a tab after a path that contains a space
        if name == "/dev/null":
            return None
        if not name.startswith(prefix):
            raise GitFailed("`git diff` gave a file line that cannot be read (expected `%s %s<path>`): %s" % (row[:3], prefix, row[:200]))
        return name[len(prefix):]

    for row in diff.splitlines():
        if row.startswith("diff --git "):
            in_header, old, current = True, None, None
            continue
        if row.startswith("@@"):
            in_header = False
        elif not in_header:
            continue                                     # a deleted line "-- ..." may look like "--- ..." but is not a header
        if row.startswith("--- "):
            old = side(row, "a/")
            continue
        if row.startswith("+++ "):
            new = side(row, "b/")
            # a deleted file (no new side) or a newly added file (no old side) cannot be matched by line — whole file (None): every test that passed through that file
            if new is None or old is None:
                found[new or old] = None
                current = None
            else:
                current = new
                found.setdefault(current, set())
            continue
        m = re.match(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", row)
        if m and current and found.get(current) is not None:
            start, count = int(m.group(1)), int(m.group(2) if m.group(2) is not None else 1)
            if count:
                found[current].update(range(start, start + count))
            else:
                found[current].update((start, start + 1))
                new_count = int(m.group(4) if m.group(4) is not None else 1)
                added.setdefault(current, []).append(new_count)
                ranges.setdefault(current, []).append([int(m.group(3)), int(m.group(3)) + new_count - 1])
    untracked = git_text(repo, ["ls-files", "--others", "--exclude-standard"]).split("\n") if range_ == "working" else []     # a failure is not "no new file" — git_text raises
    for name in untracked:
        if name.strip():
            found[name.strip()] = None
    return found, added, ranges


def load_map(repo):
    """(map | None, problem | None) — problem is None when there is no map file at all, and says what is wrong when the
    file exists but cannot be used (unreadable, not JSON, or not a map: an object with a "tests" table)"""
    path = repo / ".oqa" / "impact-map.json"
    if not path.is_file():
        return None, None
    try:
        map_ = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        return None, "cannot be read as JSON (%s)" % str(error)[:120]
    if not isinstance(map_, dict) or not isinstance(map_.get("tests"), dict):
        return None, "is not an impact map (no \"tests\" table)"
    return map_, None


def read_map(repo):
    """The map, or None when there is no usable map (no file, or a damaged one — load_map tells the two apart)"""
    return load_map(repo)[0]


def stale_reason(map_, repo, range_="working"):
    """Why the map must not be matched against the current lines — None when there is no reason. Shared by select and the trace tool"""
    if map_.get("HEAD") != head(repo):
        return "differs from the current HEAD"
    if map_.get("modified"):
        return "recorded with modified .py files"
    if range_ != "working":
        return "range %s does not line up with the HEAD-based map" % range_
    return None


def modified_py(repo):
    """Tracked .py files that differ from HEAD (names as they are — split by line).
    When git cannot say (no repository, no commit yet, git missing) the answer is not an empty list, which would read as
    "nothing modified": it is one entry saying that the git command failed, so the callers treat the state as modified"""
    try:
        return [ln for ln in git_text(repo, ["diff"] + DIFF_OPTIONS + ["HEAD", "--name-only", "--", "*.py"]).splitlines() if ln.strip()]
    except GitFailed as error:
        return ["(unknown — %s)" % error]


def _listed_new_tests(repo, new_files):
    """Those of the new files that carry a test name (`test_*.py` · `*_test.py`) and are named by the command of an entry of the test list —
    the files report.py runs itself. Only for the wording of the printed line; when the list cannot be read, none"""
    if not new_files:
        return set()
    here = str(Path(__file__).resolve().parent)
    if here not in sys.path:
        sys.path.insert(0, here)
    from init import is_test_name
    from traceability import tests_for
    try:
        # the entries as written (a "cases" entry is not expanded: its command names the file all the same, and expanding would start the test)
        entries = json.loads((Path(repo) / ".oqa" / "tests.json").read_text(encoding="utf-8-sig"))
        tests = [t for t in entries if isinstance(t, dict) and t.get("name") and isinstance(t.get("command"), str)]
    except (OSError, ValueError, TypeError):
        return set()
    return {file for file in new_files if is_test_name(file) and tests_for(tests, file, None, repo)}


def select(repo, range_, run_selected, fail_pattern, json_path=None):
    map_, problem = load_map(repo)
    if map_ is None and problem:
        print("the impact map file is damaged — %s %s. Run `impact.py record` again (a damaged map does not say \"0 tests selected\")" % (repo / ".oqa" / "impact-map.json", problem))
        return 2
    if map_ is None:
        print("no impact map — run `impact.py record` first (without a map this does not say \"0 tests selected\")")
        return 2
    reason = stale_reason(map_, repo, range_)
    stale = bool(reason)
    try:
        changed, added, added_ranges = changed_old_lines(repo, range_)
    except GitFailed as error:
        # the change could not be measured — that is not "nothing changed, 0 tests selected"
        print("cannot judge — the change could not be read from git: %s" % error)
        return 2
    # Nothing under .oqa/ is part of the change (the spec, the test list, the map, the evidence — the same yardstick as the fingerprint and
    # core.py's list of changes): such files were listed as "unseen" and counted as changed files, so the report said 5 where the plan said 3
    changed = {file: lines for file, lines in changed.items() if not file.startswith(".oqa/")}
    selected, gaps, unseen = {}, {}, []
    for file, lines in changed.items():
        if not file.endswith(".py"):
            unseen.append(file)
            continue
        covered = set()
        for name, files in map_["tests"].items():
            passed = set(files.get(file, []))
            if passed and (lines is None or passed & lines):
                selected.setdefault(name, set()).add(file)
                covered |= (passed if lines is None else passed & lines)
        if lines is None:
            # a whole file: new, or deleted. A deleted file has no lines left to cover — the tests that passed through it are selected above, and
            # that is all there is to check (it used to be reported as a gap "new file", which no test could ever lift)
            if not covered and (Path(repo) / file).is_file():
                gaps[file] = "new file — no test passed through it"
        else:
            bare = sorted(lines - covered)
            # lines that never execute (blank lines, comments, def headers) cannot be covered — report a gap only when not a single changed line was passed through
            if not covered:
                gaps[file] = "no test passed through changed lines %s" % (bare[:8] if len(bare) <= 8 else str(bare[:8])[:-1] + ", ...]")
            elif file in added:
                # even when the neighbouring lines are covered, newly added lines appeared after recording and are not in the map — do not call them covered
                # (the report matches on the leading "<n> added line(s)" — keep that form)
                gaps[file] = "%d added line(s) — added after the map was recorded, so the map cannot say which tests pass through them; they are settled later in the same report by the mutants on those lines" % sum(added[file])
    # A test that did not pass while the map was recorded stopped somewhere: the map holds only the lines before that point, so it would never
    # be selected for a change behind it — and a failing listed test was then not run at all ("selected 1 / total 2 … all passed"). Such a test
    # is selected for every change, so it runs now and its result counts
    failed_at_record = sorted(name for name, result in (map_.get("results") or {}).items() if result != "pass" and name in map_["tests"])
    for name in (failed_at_record if changed else []):
        selected.setdefault(name, set())
    print("impact map: %s (HEAD %s)%s" % (map_.get("created"), (map_.get("HEAD") or "?")[:7], (" — **stale: %s, record again**" % reason) if stale else ""))
    if failed_at_record and changed:
        print("  selected whatever changed — did not pass when the map was recorded, so the map cannot say which lines they reach: %s" % ", ".join(failed_at_record))
    print("selected tests %d / total %d: %s" % (len(selected), len(map_["tests"]), ", ".join(sorted(selected)) or "none"))
    listed_new = _listed_new_tests(repo, [file for file, reason in gaps.items() if reason.startswith("new file")])
    for file, reason in gaps.items():
        if file in listed_new:
            # still in "gaps" of the result (report.py settles it there) — but printed for what it is: a raw "gap … new file" read as a defect
            print("  new test file %s — listed in .oqa/tests.json; it is in no map (recorded before the file existed), so report.py runs it and settles it by its own result" % file)
        else:
            print("  gap %s — %s" % (file, reason))
    if unseen:
        print("  unseen (non-Python) %d: %s" % (len(unseen), ", ".join(unseen[:6]) + (" ..." if len(unseen) > 6 else "")))
    results, seconds = {}, {}
    if run_selected:
        tests = {t["name"]: t for t in test_list(repo)}
        # A test the map knows but the list no longer has (removed or renamed, often as part of this very change) cannot be run. It used to get the
        # result "not in list", which counted as a failed test that nothing but a separate commit and a new record could clear. It is left out and
        # said; the lines only it reached show up as a gap or a surviving mutant, like any other line no test covers
        gone = sorted(name for name in selected if name not in tests)
        for name in gone:
            del selected[name]
        if gone:
            print("  left out — in the map but no longer in .oqa/tests.json (removed or renamed): %s" % ", ".join(gone))
        # An entry whose command is not the one the map was recorded with (`init.py --force` gave the name to another file, or the command was
        # edited): the recorded lines belong to the old command, so they cannot say when this entry is needed. It is run for every change
        recorded_commands = map_.get("commands") or {}
        other_command = sorted(name for name in tests if name in map_["tests"] and recorded_commands.get(name) not in (None, tests[name]["command"]))
        for name in (other_command if changed else []):
            selected.setdefault(name, set())
        if other_command and changed:
            print("  selected whatever changed — the command is not the one the map was recorded with: %s" % ", ".join(other_command))
        for name in sorted(selected):
            test = tests[name]
            started = time.time()
            code, output = run(test["command"], repo)
            results[name] = "fail" if code is None or code != 0 or re.search(test.get("fail_pattern") or fail_pattern, output) else "pass"
            seconds[name] = round(time.time() - started, 1)     # the mutation tool uses it to run fast tests first
            print("  %-14s %s %5.1fs" % (name, results[name], seconds[name]))
    if json_path:
        Path(json_path).write_text(json.dumps({"map_stale": stale, "changed": sorted(changed), "selected": {k: sorted(v) for k, v in selected.items()}, "total": len(map_["tests"]),
                                               "gaps": gaps, "added_ranges": {f: r for f, r in added_ranges.items() if f in gaps}, "unseen": unseen, "results": results, "seconds": seconds},
                                              ensure_ascii=False, indent=1), encoding="utf-8")
    return 1 if gaps or any(r != "pass" for r in results.values()) else 0


def main(argv=None):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from mutate import DEFAULT_FAIL_PATTERN
    parser = argparse.ArgumentParser(description="Record which test executes which line, and select only the tests that pass through changed lines")
    parser.add_argument("action", choices=("record", "select"))
    parser.add_argument("--repo", required=True)
    parser.add_argument("--range", default="working")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--fail-pattern", default=DEFAULT_FAIL_PATTERN)
    parser.add_argument("--json")
    args = parser.parse_args(argv)
    repo = Path(args.repo).resolve()
    if args.action == "record":
        return record(repo, args.fail_pattern)
    return select(repo, args.range, args.run, args.fail_pattern, args.json)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
