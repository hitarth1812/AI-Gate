import importlib.util
import json
import marshal
import os
import py_compile
import re
import shutil
import stat
import subprocess
import sys

import pytest

from conftest import SCRIPT, aigate

VERSION = aigate.__version__

POSIX = os.name != "nt"
LOADER = aigate.HOOK_LOADER
posix_only = pytest.mark.skipif(not POSIX, reason="needs POSIX ownership and mode bits")
not_root = pytest.mark.skipif(POSIX and os.geteuid() == 0, reason="root ignores directory permissions")
windows_only = pytest.mark.skipif(POSIX, reason="the Windows default cache location")
needs_sh = pytest.mark.skipif(shutil.which("sh") is None, reason="no POSIX sh on this machine")


def copy_script(tmp_path, folder="gate"):
    dest = tmp_path / folder / "aigate_lite.py"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(SCRIPT, dest)
    return dest


def env_for(repo, cache):
    return {**repo.env, "AIGATE_CACHE_DIR": str(cache)}


def via_loader(script, env, *args, cwd=None):
    return subprocess.run([sys.executable, "-P", "-c", LOADER, str(script), *args], cwd=cwd, env=env, text=True,
                          capture_output=True)


def direct(script, env, *args, cwd=None):
    return subprocess.run([sys.executable, str(script), *args], cwd=cwd, env=env, text=True, capture_output=True)


def pycs(cache):
    return sorted(p for p in (cache / "pycache").rglob("*.pyc")) if (cache / "pycache").exists() else []


def cache_path_for(script, prefix):
    old = sys.pycache_prefix
    sys.pycache_prefix = None if prefix is None else str(prefix)
    try:
        return importlib.util.cache_from_source(str(script))
    finally:
        sys.pycache_prefix = old


def plant(evil_dir, target, mode=py_compile.PycInvalidationMode.UNCHECKED_HASH):
    evil = evil_dir / "evil.py"
    evil.write_text('print("PWNED")\nimport sys\nsys.exit(0)\n')
    os.makedirs(os.path.dirname(target), exist_ok=True)
    py_compile.compile(str(evil), cfile=target, doraise=True, invalidation_mode=mode)


def normal(proc):
    out = re.sub(r"\d+\.\ds\b", "Ns", proc.stdout)
    if out.startswith("{"):
        data = json.loads(out)
        data["stats"].pop("seconds", None)
        out = json.dumps(data, sort_keys=True)
    return proc.returncode, out, proc.stderr


def test_loader_output_matches_a_direct_run(repo, tmp_path):
    script, cache = copy_script(tmp_path), tmp_path / "pyc-cache"
    env = env_for(repo, cache)
    repo.write("a.py", "eval(x)\n")
    repo.add()
    for args in (["--version"], ["check", "--format", "json"], ["check", "--color", "never"], ["explain"]):
        want = normal(direct(script, env, *args, cwd=repo.path))
        assert normal(via_loader(script, env, *args, cwd=repo.path)) == want            # cold bytecode cache
        assert normal(via_loader(script, env, *args, cwd=repo.path)) == want            # warm
    assert len(pycs(cache)) == 1


def test_bytecode_is_checked_hash_in_a_private_dir_and_reused(repo, tmp_path):
    script, cache = copy_script(tmp_path), tmp_path / "pyc-cache"
    env = env_for(repo, cache)
    via_loader(script, env, "--version")
    [pyc] = pycs(cache)
    assert pyc.read_bytes()[4:8] == b"\x03\x00\x00\x00"                     # hash-based, checked against source
    if POSIX:
        assert stat.S_IMODE(os.stat(cache / "pycache").st_mode) == 0o700
    before = os.stat(pyc).st_mtime_ns
    assert via_loader(script, env, "--version").stdout.strip() == f"aigate-lite {VERSION}"
    assert os.stat(pyc).st_mtime_ns == before                                # reused, not rewritten


def test_same_size_same_second_edit_is_recompiled(repo, tmp_path):
    script, cache = copy_script(tmp_path), tmp_path / "pyc-cache"
    env = env_for(repo, cache)
    assert via_loader(script, env, "--version").stdout.strip() == f"aigate-lite {VERSION}"
    st = os.stat(script)
    script.write_bytes(script.read_bytes().replace(f'__version__ = "{VERSION}"'.encode(), b'__version__ = "9.9.9"'))
    os.utime(script, ns=(st.st_atime_ns, st.st_mtime_ns))                    # a timestamp pyc would not notice
    assert os.stat(script).st_size == st.st_size
    assert via_loader(script, env, "--version").stdout.strip() == "aigate-lite 9.9.9"


def test_unusable_cache_dir_falls_back_silently(repo, tmp_path):
    script = copy_script(tmp_path)
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")
    proc = via_loader(script, env_for(repo, blocker), "--version")
    assert (proc.returncode, proc.stdout.strip(), proc.stderr) == (0, f"aigate-lite {VERSION}", "")
    assert not (script.parent / "__pycache__").exists()


@posix_only
@not_root
def test_read_only_cache_dir_falls_back_silently(repo, tmp_path):
    script, cache = copy_script(tmp_path), tmp_path / "ro-cache"
    cache.mkdir(mode=0o500)
    try:
        proc = via_loader(script, env_for(repo, cache), "--version")
    finally:
        cache.chmod(0o700)
    assert (proc.returncode, proc.stdout.strip(), proc.stderr) == (0, f"aigate-lite {VERSION}", "")
    assert pycs(cache) == []


def test_paths_with_spaces_and_unicode(repo, tmp_path):
    script, cache = copy_script(tmp_path, "dir with spaces ü"), tmp_path / "cache ü x"
    env = env_for(repo, cache)
    for _ in range(2):
        proc = via_loader(script, env, "--version")
        assert (proc.returncode, proc.stdout.strip(), proc.stderr) == (0, f"aigate-lite {VERSION}", "")
    assert len(pycs(cache)) == 1


def test_bytecode_planted_next_to_the_script_is_never_run(repo, tmp_path):
    # a vendored .aigate/__pycache__ comes from the repository; neither mode may load it
    script, cache = copy_script(tmp_path), tmp_path / "pyc-cache"
    plant(tmp_path, cache_path_for(script, None))
    for env in (env_for(repo, cache), env_for(repo, tmp_path / "missing" / "x")):
        proc = via_loader(script, env, "--version")
        assert "PWNED" not in proc.stdout and proc.stdout.strip() == f"aigate-lite {VERSION}"


def test_unchecked_bytecode_in_the_cache_is_replaced_not_run(repo, tmp_path):
    script, cache = copy_script(tmp_path), tmp_path / "pyc-cache"
    env = env_for(repo, cache)
    via_loader(script, env, "--version")
    [pyc] = pycs(cache)
    plant(tmp_path, str(pyc))
    proc = via_loader(script, env, "--version")
    assert "PWNED" not in proc.stdout and proc.stdout.strip() == f"aigate-lite {VERSION}"
    assert pyc.read_bytes()[4:8] == b"\x03\x00\x00\x00"


def forge(script, pyc, obj):
    """A .pyc whose header is valid for the current source but whose payload is `obj` (stands in for an
    attacker's file); written in place, so the file keeps its mode and links."""
    header = importlib.util.MAGIC_NUMBER + b"\x03\x00\x00\x00" + importlib.util.source_hash(script.read_bytes())
    with open(pyc, "r+b") as fh:
        fh.truncate(0)
        fh.write(header + marshal.dumps(obj))


EVIL = compile('print("PWNED")', "evil", "exec")


def warmed(repo, tmp_path):
    script, cache = copy_script(tmp_path), tmp_path / "pyc-cache"
    env = env_for(repo, cache)
    via_loader(script, env, "--version")
    [pyc] = pycs(cache)
    return script, cache, env, pyc


def clean_version(proc):
    return (proc.returncode, proc.stdout.strip(), proc.stderr) == (0, f"aigate-lite {VERSION}", "")


@posix_only
def test_symlinked_pycache_dir_is_not_followed(repo, tmp_path):
    script, cache = copy_script(tmp_path), tmp_path / "pyc-cache"
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir(mode=0o700)
    cache.mkdir()
    (cache / "pycache").symlink_to(elsewhere, target_is_directory=True)
    assert clean_version(via_loader(script, env_for(repo, cache), "--version"))
    assert not list(elsewhere.iterdir())


@posix_only
def test_loose_pycache_is_tightened_and_its_files_not_trusted_that_run(repo, tmp_path):
    # another account could have written into it while it was loose
    script, cache, env, pyc = warmed(repo, tmp_path)
    forge(script, pyc, EVIL)
    os.chmod(cache / "pycache", 0o777)
    proc = via_loader(script, env, "--version")
    assert clean_version(proc) and stat.S_IMODE(os.stat(cache / "pycache").st_mode) == 0o700
    assert clean_version(via_loader(script, env, "--version"))              # the forged file was replaced


@posix_only
def test_writable_or_hard_linked_pyc_is_not_trusted(repo, tmp_path):
    script, cache, env, pyc = warmed(repo, tmp_path)
    forge(script, pyc, EVIL)
    os.chmod(pyc, 0o666)
    assert clean_version(via_loader(script, env, "--version"))
    [pyc] = pycs(cache)
    forge(script, pyc, EVIL)
    os.link(pyc, tmp_path / "second-name")
    assert clean_version(via_loader(script, env, "--version"))


def test_a_payload_that_is_not_code_falls_back_silently(repo, tmp_path):
    script, cache, env, pyc = warmed(repo, tmp_path)
    forge(script, pyc, 42)
    assert clean_version(via_loader(script, env, "--version"))


@posix_only
def test_a_shared_cache_dir_is_left_alone(repo, tmp_path):
    script, cache = copy_script(tmp_path), tmp_path / "pyc-cache"
    cache.mkdir()
    os.chmod(cache, 0o777)
    env = env_for(repo, cache)
    for _ in range(2):
        assert clean_version(via_loader(script, env, "--version"))
    assert stat.S_IMODE(os.stat(cache).st_mode) == 0o777 and len(pycs(cache)) == 1


def test_relative_cache_dirs_are_never_used(repo, tmp_path):
    # e.g. no HOME and no passwd entry: "~" stays relative and would land inside the repository
    script, home = copy_script(tmp_path), tmp_path / "home"
    base = {k: v for k, v in repo.env.items() if k != "AIGATE_CACHE_DIR"}
    for extra in ({"AIGATE_CACHE_DIR": "rel-cache"}, {"XDG_CACHE_HOME": "rel-xdg"}):
        local = {"LOCALAPPDATA": str(home / "AppData" / "Local")}              # Windows' default cache base
        proc = via_loader(script, {**base, "HOME": str(home), **local, **extra}, "--version", cwd=repo.path)
        assert clean_version(proc)
    assert repo.git("status", "--porcelain", "--ignored").stdout == ""
    default = home / ".cache" / "aigate-lite" if POSIX else home / "AppData" / "Local" / "aigate-lite"
    assert len(pycs(default)) == 1                                           # relative XDG is ignored


@posix_only
def test_symlinked_script_never_imports_from_the_link_directory(repo, tmp_path):
    # was ..._puts_its_real_directory_on_sys_path: F4 removed the sys.path insertion; the property that matters
    # (a shadowing module beside the symlink is never imported, directly or through the loader) is unchanged
    real = copy_script(tmp_path, "real")
    links = tmp_path / "bin"
    links.mkdir()
    (links / "argparse.py").write_text(SHADOW)
    (links / "aigate_lite.py").symlink_to(real)
    env = env_for(repo, tmp_path / "pyc-cache")
    for run in (direct, via_loader):
        assert clean_version(run(links / "aigate_lite.py", env, "--version"))


def test_old_bytecode_is_pruned_when_a_new_file_is_written(repo, tmp_path):
    script, cache, env, pyc = warmed(repo, tmp_path)
    old, recent = pyc.with_name("aigate_lite.0.old.pyc"), pyc.with_name("aigate_lite.1.recent.pyc")
    stale_tmp = pyc.with_name("aigate_lite.2.x.pyc.123.tmp")
    for p, days in ((old, 40), (recent, 2), (stale_tmp, 40)):
        p.write_bytes(b"x")
        t = os.stat(pyc).st_mtime - days * 86400
        os.utime(p, (t, t))
    pyc.unlink()                                                             # force a new write
    assert clean_version(via_loader(script, env, "--version"))
    assert not old.exists() and not stale_tmp.exists() and recent.exists() and pyc.exists()


def test_hook_uses_the_loader_on_every_os():
    # was test_hook_uses_the_loader_only_on_posix (F4: Windows hooks reuse bytecode too)
    for vendored in (True, False):
        text = aigate.hook_script("pre-commit", vendored, sys.executable, str(SCRIPT))
        assert "-P -c" in text and "LOADER=" in text


SHADOW = 'print("PWNED")\n'


def test_modules_in_the_repo_root_cannot_shadow_the_stdlib(repo, tmp_path):
    script = copy_script(tmp_path)
    for name in ("argparse", "json", "subprocess", "hashlib"):
        repo.write(f"{name}.py", SHADOW)
    env = env_for(repo, tmp_path / "pyc-cache")
    control = subprocess.run([sys.executable, "-c", LOADER, str(script), "--version"], cwd=repo.path, env=env,
                             text=True, capture_output=True)
    assert "PWNED" in control.stdout                                         # without -P the cwd is on sys.path
    for args in (["--version"], ["check", "--format", "json"]):
        assert "PWNED" not in via_loader(script, env, *args, cwd=repo.path).stdout


@needs_sh
def test_installed_hook_reuses_bytecode_and_ignores_the_repo_root(repo, tmp_path):
    from test_hooks import REAL, fake_bin, install_vendored, run_hook
    install_vendored(repo)
    repo.write("json.py", SHADOW)
    repo.write("ok.py", "def add(a, b):\n    return a + b\n")
    repo.add()
    bin_dir = fake_bin(tmp_path, {"python3": REAL})
    for _ in range(2):
        proc = run_hook(repo, "pre-commit", bin_dir)
        assert proc.returncode == 0 and "PWNED" not in proc.stdout, proc.stdout + proc.stderr
    from pathlib import Path
    assert len(pycs(Path(repo.env["AIGATE_CACHE_DIR"]))) == 1


# ---------------------------------------------------------------- F4: agents, repository paths, one loader for all OSes

def test_loader_agent_variables_match_the_gates_list():
    m = re.search(r"^AGENT_ENV = \((.*?)\)$", LOADER, re.M)
    assert tuple(re.findall(r'"([A-Z_]+)"', m.group(1))) == aigate.BUILTIN_AGENT_ENV


@pytest.mark.parametrize("var", [{"CLAUDECODE": "1"}, {"AI_AGENT": "x"}, {"GEMINI_CLI": "1"}, {"AIGATE_AGENT": "1"},
                                 {"AIGATE_PROVENANCE": "agent"}])
def test_agents_never_read_or_write_the_bytecode_cache(repo, tmp_path, var):
    script, cache = copy_script(tmp_path), tmp_path / "pyc-cache"
    env = {k: v for k, v in env_for(repo, cache).items() if k not in aigate.BUILTIN_AGENT_ENV}
    via_loader(script, env, "--version")                                     # a human run fills the cache
    [pyc] = pycs(cache)
    plant(tmp_path, str(pyc))                                                # and someone then replaces it
    before = pyc.read_bytes()
    proc = via_loader(script, {**env, **var}, "--version")
    assert "PWNED" not in proc.stdout and proc.stdout.strip() == f"aigate-lite {VERSION}"
    assert pyc.read_bytes() == before                                        # not read (a human run would replace it)
    assert pycs(cache) == [pyc]                                              # and nothing new written
    fresh = tmp_path / "fresh-cache"
    assert clean_version(via_loader(script, {**env, **var, "AIGATE_CACHE_DIR": str(fresh)}, "--version"))
    assert not fresh.exists()


def test_a_cache_dir_inside_the_repository_is_refused(repo, tmp_path):
    script = copy_script(tmp_path)
    for inside in (repo.path / ".cache", repo.path / "deep" / "er"):
        proc = via_loader(script, env_for(repo, inside), "--version", cwd=repo.path)
        assert clean_version(proc)
        assert not inside.exists()                                           # not even created
    assert repo.git("status", "--porcelain", "--ignored").stdout == ""


def test_a_cache_dir_beside_the_script_is_refused(repo, tmp_path):
    script = copy_script(tmp_path, "repo-like/.aigate")
    cache = script.parent / "pyc"
    assert clean_version(via_loader(script, env_for(repo, cache), "--version", cwd=tmp_path))
    assert not cache.exists()


def test_the_loader_does_not_change_sys_path_and_gives_the_module_a_spec(repo, tmp_path):
    script, cache = copy_script(tmp_path), tmp_path / "pyc-cache"
    code = LOADER.replace("sys.exit(module.main())",
                          "print(sys.path[:1], module.__spec__.name, module.__spec__.origin == script)")
    proc = subprocess.run([sys.executable, "-P", "-c", code, str(script), "--version"], env=env_for(repo, cache),
                          text=True, capture_output=True)
    assert proc.returncode == 0, proc.stderr
    last = proc.stdout.strip().splitlines()[-1]
    assert str(script.parent) not in last and last.endswith("aigate_lite True")


@windows_only
def test_windows_default_cache_dir_comes_from_localappdata(repo, tmp_path):
    script, local = copy_script(tmp_path), tmp_path / "Local"
    env = {k: v for k, v in repo.env.items() if k != "AIGATE_CACHE_DIR"}
    assert clean_version(via_loader(script, {**env, "LOCALAPPDATA": str(local)}, "--version", cwd=repo.path))
    assert len(pycs(local / "aigate-lite")) == 1


@pytest.mark.parametrize("value", ["Agent", " AGENT ", "agent\t"])
def test_provenance_spelling_variants_still_count_as_an_agent(repo, tmp_path, value):
    script, cache = copy_script(tmp_path), tmp_path / "pyc-cache"
    env = {k: v for k, v in env_for(repo, cache).items() if k not in aigate.BUILTIN_AGENT_ENV}
    assert clean_version(via_loader(script, {**env, "AIGATE_PROVENANCE": value}, "--version"))
    assert not cache.exists()


@posix_only
def test_a_deleted_working_directory_just_disables_the_cache(repo, tmp_path):
    script, cache = copy_script(tmp_path), tmp_path / "pyc-cache"
    driver = ("import os, sys; d = sys.argv.pop(1); os.mkdir(d); os.chdir(d); os.rmdir(d); exec(" + repr(LOADER) + ")")
    proc = subprocess.run([sys.executable, "-P", "-c", driver, str(tmp_path / "gone"), str(script), "--version"],
                          env=env_for(repo, cache), text=True, capture_output=True)
    assert proc.stdout.strip() == f"aigate-lite {VERSION}" and "Traceback" not in proc.stderr, proc.stderr
