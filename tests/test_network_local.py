"""0.5.1 Part B: a local error (an unwritable SSLKEYLOGFILE, an unwritable cache) is not a network outage.

Seen on Windows 11 / Python 3.12 / 0.5.0: SSLKEYLOGFILE pointed at an unwritable `\\\\?\\Volume{...}\\virtual_file.log`,
ssl.create_default_context() raised PermissionError while opening it, every urlopen() failed the same way, and the
report turned 13 real deps.vulnerable findings into 29 "could not reach the Go module proxy" warnings.
No test here touches the network: urlopen is replaced in-process, or a proxy on a closed local port refuses."""
import io
import json
import os
import ssl
import sys
import urllib.error
import urllib.request

import pytest

from conftest import aigate, find

WINDOWS = os.name == "nt"


@pytest.fixture(autouse=True)
def no_keylog(monkeypatch):
    """Start from no SSLKEYLOGFILE (the machine may set one); `bad_keylog` sets an unwritable one."""
    monkeypatch.delenv("SSLKEYLOGFILE", raising=False)


@pytest.fixture
def fresh_tls(monkeypatch):
    """Each test builds its own TLS context (the module keeps one per run)."""
    monkeypatch.setattr(aigate, "_TLS", {})
    return aigate._TLS


@pytest.fixture
def bad_keylog(tmp_path, monkeypatch):
    path = tmp_path / "no-such-dir" / "keys.log"            # the parent is missing: opening it fails on every OS
    monkeypatch.setenv("SSLKEYLOGFILE", str(path))
    return str(path)


def test_the_cause_reproduced_ssl_default_context_raises_on_an_unwritable_keylog_file(bad_keylog):
    with pytest.raises(OSError):
        ssl.create_default_context()
    with pytest.raises(OSError):                     # and urlopen without a context builds that same context
        urllib.request.urlopen("https://pypi.org/simple/", timeout=0.01)


def test_tls_context_falls_back_with_verification_on_and_one_note(fresh_tls, bad_keylog):
    ctx = aigate.tls_context()
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname is True
    assert aigate.tls_context() is ctx                                # one context per run
    assert fresh_tls["note"].startswith(f"SSLKEYLOGFILE={bad_keylog} cannot be written; continuing without TLS key logging")


def test_tls_context_is_the_plain_default_when_nothing_is_wrong(fresh_tls, monkeypatch):
    monkeypatch.delenv("SSLKEYLOGFILE", raising=False)
    ctx = aigate.tls_context()
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname is True and "note" not in fresh_tls


class FakeResponse(io.BytesIO):
    def __init__(self, body, status=200):
        super().__init__(json.dumps(body).encode())
        self.status, self.headers = status, {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def pypi_answer(url):
    if "/pypi/" in url:
        return {"info": {"name": "requests", "version": "2.32.3"},
                "releases": {"2.32.3": [{"upload_time_iso_8601": "2024-05-29T15:37:47Z"}]}}
    if "osv.dev" in url:
        return {"vulns": [{"id": "GHSA-test-0001"}]}
    raise AssertionError(url)


@pytest.fixture
def fake_urlopen(monkeypatch):
    seen = []

    def urlopen(req, timeout=None, context=None):
        seen.append(context)
        if context is None:                              # what 0.5.0 did: the default context, which raises here
            ssl.create_default_context()
        return FakeResponse(pypi_answer(req.full_url))
    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    return seen


def test_lookups_succeed_with_an_unwritable_keylog_and_pass_the_verifying_context(fresh_tls, bad_keylog,
                                                                                    fake_urlopen, tmp_path):
    reg = aigate.Registry(use_cache=False)
    assert reg.info("pypi", "requests")["latest"] == "2.32.3"
    assert reg.vulns("pypi", "requests", "2.32.3") == ["GHSA-test-0001"]
    assert fake_urlopen and all(c is not None and c.verify_mode == ssl.CERT_REQUIRED and c.check_hostname
                                for c in fake_urlopen)
    assert reg.failures == 0 and reg.local_failures == 0
    notes = aigate.registry_notes(reg)
    assert [n for n in notes if "SSLKEYLOGFILE" in n] == [notes[0]] and len(notes) == 1


def test_a_local_error_is_not_counted_as_a_network_failure(fresh_tls, monkeypatch):
    def urlopen(req, timeout=None, context=None):
        raise PermissionError(13, "Permission denied", r"\\?\Volume{0}\virtual_file.log")
    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    reg = aigate.Registry(use_cache=False)
    assert reg.info("go", "github.com/a/b") is None
    assert reg.failures == 0 and reg.local_failures == 1 and "github.com/a/b" in reg.local_names
    note = " ".join(aigate.registry_notes(reg))
    assert "on this machine, not on the network" in note and "did NOT run" in note
    assert "not reachable" not in note and "could not reach" not in note


def test_a_real_network_failure_is_still_a_network_failure(fresh_tls, monkeypatch):
    def urlopen(req, timeout=None, context=None):
        raise urllib.error.URLError(ConnectionRefusedError(111, "Connection refused"))
    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    reg = aigate.Registry(use_cache=False)
    assert reg.info("pypi", "requests") is None
    assert reg.failures == 1 and reg.local_failures == 0 and "connection refused" in reg.failure_reason


@pytest.mark.parametrize("error, local", [
    (PermissionError(13, "Permission denied", "x.log"), True),
    (FileNotFoundError(2, "No such file", "x.log"), True),
    (urllib.error.URLError(ConnectionRefusedError()), False),
    (urllib.error.URLError(PermissionError(10013, "forbidden by its access permissions")), False),   # a firewall
    (TimeoutError(), False),
    (ConnectionResetError(), False),
    (ssl.SSLError(), False),
])
def test_error_classification(error, local):
    assert aigate.is_local_error(error) is local


# ---- end to end (subprocess): the real urlopen path with SSLKEYLOGFILE unwritable --------------------------------

CLOSED = {"AIGATE_REGISTRY_FIXTURE": "", "HTTPS_PROXY": "http://127.0.0.1:9", "HTTP_PROXY": "http://127.0.0.1:9",
          "https_proxy": "http://127.0.0.1:9", "http_proxy": "http://127.0.0.1:9", "NO_PROXY": "", "no_proxy": ""}


def deps_change(repo):
    repo.write("requirements.txt", "requests\n")
    repo.write("app.py", "import requests\n")
    repo.add()


def test_unwritable_keylog_end_to_end_one_tls_note_and_the_real_network_error(repo, tmp_path):
    deps_change(repo)
    keylog = str(tmp_path / "missing" / "keys.log")
    res = repo.check("--no-cache", env={**CLOSED, "SSLKEYLOGFILE": keylog})
    tls = [n for n in res["notes"] if "SSLKEYLOGFILE" in n]
    assert tls == [f"SSLKEYLOGFILE={keylog} cannot be written; continuing without TLS key logging "
                   "(FileNotFoundError; fix: unset SSLKEYLOGFILE or point it at a writable file)"]
    net = [n for n in res["notes"] if "registry lookup" in n]
    assert len(net) == 1 and "connection refused" in net[0]            # the proxy really refused: a network failure
    assert find(res, "deps.unverified") and "PermissionError" not in json.dumps(res) \
        and "FileNotFoundError: [Errno" not in json.dumps(res)


@pytest.mark.parametrize("make_bad", ["read_only_dir", "parent_is_a_file"])
def test_unwritable_cache_one_note_lookups_still_run(fresh_tls, monkeypatch, tmp_path, fake_urlopen, make_bad):
    if make_bad == "read_only_dir":
        if WINDOWS or (hasattr(os, "geteuid") and os.geteuid() == 0):
            pytest.skip("a read-only directory is not enforced here (Windows ACLs / root)")
        cache = tmp_path / "ro"
        cache.mkdir()
        os.chmod(cache, 0o500)
    else:
        (tmp_path / "file").write_text("x")
        cache = tmp_path / "file" / "cache"                  # a parent that is a file: no folder can be made
    monkeypatch.setenv("AIGATE_CACHE_DIR", str(cache))
    try:
        reg = aigate.Registry()
        assert reg.info("pypi", "requests")["latest"] == "2.32.3"           # the lookup ran
        assert reg.info("pypi", "requests")["latest"] == "2.32.3" and len(fake_urlopen) == 1   # memory cache
        reg.save()
    finally:
        if make_bad == "read_only_dir":
            os.chmod(cache, 0o700)
    notes = [n for n in aigate.registry_notes(reg) if "AIGATE_CACHE_DIR" in n]
    assert len(notes) == 1 and str(cache) in notes[0] and reg.failures == reg.local_failures == 0


def test_cache_parent_missing_is_created(fresh_tls, monkeypatch, tmp_path, fake_urlopen):
    cache = tmp_path / "a" / "b" / "c"
    monkeypatch.setenv("AIGATE_CACHE_DIR", str(cache))
    reg = aigate.Registry()
    reg.info("pypi", "requests")
    reg.save()
    assert (cache / "registry-v1.json").is_file() and aigate.registry_notes(reg) == []


def test_unwritable_cache_end_to_end_no_crash(repo, tmp_path):
    deps_change(repo)
    (tmp_path / "file").write_text("x")
    res = repo.check("--cache", env={"AIGATE_CACHE_DIR": str(tmp_path / "file" / "cache")})
    assert res["code"] in (0, 1) and not find(res, "deps.unverified")


def test_cache_write_raising_permission_error_gives_one_note(fresh_tls, monkeypatch, tmp_path, fake_urlopen):
    monkeypatch.setenv("AIGATE_CACHE_DIR", str(tmp_path / "cache"))
    reg = aigate.Registry()
    assert reg.info("pypi", "requests")["latest"] == "2.32.3"

    def deny(*a, **k):
        raise PermissionError(13, "Permission denied")
    monkeypatch.setattr(aigate.os, "replace", deny)
    reg.save()
    notes = [n for n in aigate.registry_notes(reg) if "AIGATE_CACHE_DIR" in n]
    assert len(notes) == 1 and str(tmp_path / "cache") in notes[0]
    assert not list((tmp_path / "cache").glob("*.tmp"))                # no temp file left behind


def test_cache_path_is_kept_as_given(monkeypatch):
    monkeypatch.setenv("AIGATE_CACHE_DIR", r"C:\Users\x\cache" if WINDOWS else "/tmp/x/../cache")
    assert aigate.default_cache_dir() == os.environ["AIGATE_CACHE_DIR"]


# ---- the report: a person sees one line, an agent every line -----------------------------------------------------

def unverified_result(provenance, n=5):
    fs = [aigate.make("deps.unverified", "go.mod", i + 3, f"github.com/x/m{i}: could not reach the Go module proxy",
                      key=f"m{i}") for i in range(n)]
    fs.append(aigate.make("security.eval", "app.py", 1, "eval() runs code built at runtime", key="e"))
    return aigate.Result(fs, "fix", False, 30.0, provenance, "staged changes",
                         {"files": 2, "added": 6, "removed": 0, "seconds": 0.1}, "defaults",
                         ["5 registry lookup(s) failed (connection refused); dependency checks are incomplete"])


def test_a_person_sees_one_line_for_many_unverified_packages():
    out = aigate.render_text(unverified_result(("human", "")))
    lines = [ln for ln in out.splitlines() if "deps.unverified" in ln]
    assert len(lines) == 1 and "5 packages" in lines[0] and "did NOT run" in lines[0] and "-v lists them" in lines[0]
    assert "security.eval" in out


def test_verbose_and_agent_reports_keep_every_unverified_line():
    verbose = aigate.render_text(unverified_result(("human", "")), verbose=True)
    assert sum("deps.unverified" in ln for ln in verbose.splitlines()) == 5
    agent = unverified_result(("agent", "$CLAUDECODE"))
    assert sum("deps.unverified" in ln for ln in aigate.render_text(agent).splitlines()) == 5
    assert sum(ln.startswith("WARN deps.unverified") for ln in aigate.render_agent(agent).splitlines()) == 5


def test_two_unverified_lines_are_not_folded():
    out = aigate.render_text(unverified_result(("human", ""), n=2))
    assert sum("deps.unverified" in ln for ln in out.splitlines()) == 2


# ---- agent strictness: a local failure never softens an agent's result --------------------------------------------

class Ctx:
    def __init__(self, agent, mode="warn"):
        self.cfg = {"gate": {"offline": mode}}
        self.agent = agent
        self.registry = aigate.Registry(use_cache=False, offline=True)
        self.registry.local_names = {"pkg"}
        self.registry.local_reason = "PermissionError on this machine: Permission denied"


def test_local_failure_person_default_mode_is_left_to_the_note():
    assert aigate.unverified(Ctx(agent=False), "r.txt", 1, "pkg: could not reach PyPI to verify it", "pkg") == []


@pytest.mark.parametrize("agent, mode", [(True, "warn"), (True, "block"), (False, "block")])
def test_local_failure_agent_or_block_mode_keeps_the_finding_with_the_real_cause(agent, mode):
    out = aigate.unverified(Ctx(agent=agent, mode=mode), "r.txt", 1, "pkg: could not reach PyPI to verify it", "pkg")
    assert len(out) == 1 and out[0].rule == "deps.unverified"
    assert "could not reach" not in out[0].message and "not checked: PermissionError" in out[0].message
    assert out[0].action == ("block" if mode == "block" else "warn")


def test_network_failure_unchanged_for_person_and_agent():
    for agent in (False, True):
        ctx = Ctx(agent=agent)
        out = aigate.unverified(ctx, "r.txt", 1, "other: could not reach PyPI to verify it", "other")
        assert len(out) == 1 and "could not reach PyPI" in out[0].message


# ---- doctor ---------------------------------------------------------------------------------------------------------

def test_doctor_tls_check_names_sslkeylogfile_with_both_fixes(bad_keylog):
    c = aigate._doctor_tls(dict(os.environ))
    assert c["status"] == "warn" and f"SSLKEYLOGFILE={bad_keylog}" in c["detail"]
    assert "Remove-Item Env:SSLKEYLOGFILE" in c["fix"] and "unset SSLKEYLOGFILE" in c["fix"]


def test_doctor_tls_ok_without_keylog(monkeypatch):
    monkeypatch.delenv("SSLKEYLOGFILE", raising=False)
    assert aigate._doctor_tls({})["status"] == "ok"


def test_doctor_network_local_error_is_not_not_reachable():
    def fetch(url, timeout):
        raise PermissionError(13, "Permission denied", "virtual_file.log")
    c = aigate._doctor_network({}, aigate.merge_config({}, []), fetch)
    assert "not reachable" not in c["detail"] and "on this machine" in c["detail"] and "PermissionError" in c["detail"]
    assert "check your connection" not in c["fix"]


def test_doctor_network_real_failure_names_the_exception_class():
    def fetch(url, timeout):
        raise urllib.error.URLError(ConnectionRefusedError(111, "Connection refused"))
    c = aigate._doctor_network({}, aigate.merge_config({}, []), fetch)
    assert c["detail"].startswith("not reachable:") and "ConnectionRefusedError" in c["detail"]


def test_no_urlopen_call_without_the_tls_context():
    src = open(aigate.__file__, encoding="utf-8").read()
    calls = [ln.strip() for ln in src.splitlines() if "urlopen(" in ln and "urllib.request.urlopen" in ln]
    assert calls and all("context=tls_context()" in c for c in calls), calls
    assert sys.modules["aigate_lite"] is aigate
