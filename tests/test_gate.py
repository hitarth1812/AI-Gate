import json
import os
import sys

from conftest import SCRIPT, aigate, find, rules

AWS = "AKIA" + "QWERTYUIOPASDFGH"


# ---------------------------------------------------------------- scope, provenance and policy

def test_forbidden_paths_and_agent_rules(repo):
    repo.write(".aigate/lite.toml", '[paths]\nforbidden = ["infra/prod/**"]\n')
    repo.commit()
    repo.write("infra/prod/main.tf", "x = 1\n")
    repo.write(".github/workflows/ci.yml", "on: push\n")
    repo.add()
    human = repo.check()
    assert [f["path"] for f in find(human, "scope.forbidden_path")] == ["infra/prod/main.tf"]
    assert find(human, "gate.config_changed")
    agent = repo.check(env={"CLAUDECODE": "1"})
    assert agent["provenance"] == {"kind": "agent", "source": "$CLAUDECODE"}
    assert {f["path"] for f in find(agent, "scope.forbidden_path")} == {"infra/prod/main.tf", ".github/workflows/ci.yml"}


def test_agent_cannot_loosen_the_gate_in_the_same_commit(repo):
    repo.write("svc.py", 'password = "Sup3rS3cretValue!"\n')
    repo.write(".aigate/lite.toml", '[rules]\n"secrets.*" = "off"\n')
    repo.add()
    assert not find(repo.check(), "secrets.generic")                      # a human's own config applies
    agent = repo.check(env={"AI_AGENT": "some-agent"})
    assert find(agent, "secrets.generic") and find(agent, "scope.forbidden_path")


def test_task_scope(repo):
    assert repo.run("scope", "set", "src/auth/**").returncode == 0
    repo.write("src/auth/login.py", "x = 1\n")
    repo.write("src/billing/pay.py", "y = 2\n")
    repo.add()
    assert [f["path"] for f in find(repo.check(), "scope.out_of_scope")] == ["src/billing/pay.py"]
    repo.run("scope", "clear")
    assert not find(repo.check(), "scope.out_of_scope")


def test_overrides_allow_entries_and_env_allow(repo):
    repo.write(".aigate/lite.toml", '[rules]\n"code.todo" = "off"\n"suppress.*" = "warn"\n\n'
                                    '[[allow]]\nrule = "secrets.generic"\npath = "fixtures/**"\nreason = "fake"\n\n'
                                    '[[allow]]\nrule = "secrets.hardcoded"\nreason = "missing path"\n\n'
                                    '[[allow]]\nrule = "code.swallowed_exception"\npath = "x.py"\nreason = "old"\n'
                                    'expires = 2020-01-01\n')
    repo.commit()
    repo.write("fixtures/creds.py", 'token = "Zx9kQ2vB7mN4pL1w"\n')
    repo.write("x.py", "# TODO later\ny = f()  # type: ignore\ntry:\n    g()\nexcept Exception:\n    pass\n")
    repo.add()
    res = repo.check()
    assert rules(res, allowed=True) == ["secrets.generic"]
    assert "code.todo" not in rules(res) and find(res, "suppress.type")[0]["action"] == "warn"
    problems = " ".join(f["message"] for f in find(res, "gate.config_invalid"))
    assert "need a path or a fingerprint" in problems and "expired" in problems
    once = repo.check(env={"AIGATE_ALLOW": "code.swallowed_exception"})
    assert find(once, "code.swallowed_exception")[0]["allowed"] == "AIGATE_ALLOW"
    agent = repo.check(env={"AIGATE_ALLOW": "code.swallowed_exception", "CLAUDECODE": "1"})
    assert not find(agent, "code.swallowed_exception")[0]["allowed"]


def test_env_allow_cannot_clear_blocking_findings(repo):
    repo.write("k.py", f'K = "{AWS}"\n')
    repo.add()
    res = repo.check(env={"AIGATE_ALLOW": "secrets.*"})
    assert res["verdict"] == "block" and not find(res, "secrets.hardcoded")[0]["allowed"]


def test_risk_score_formula_and_escalation():
    F = aigate.Finding
    many = [F("tests.assertions_removed", f"t{i}.py", 1, "m", "fix") for i in range(4)]   # 4 x 20 x 0.85 = 68
    assert aigate.risk_score(many, agent=False) == 68.0
    assert aigate.decide(many, 68.0) == ("block", True)
    one = many[:1]
    assert aigate.risk_score(one, agent=True) == round(1.2 * 17.0, 1)
    warns = [F("code.todo", f"w{i}.py", 1, "m", "warn") for i in range(30)]                 # warn share is capped at 25
    assert aigate.risk_score(warns, agent=False) == 25.0
    assert aigate.decide(warns, 25.0) == ("warn", False)


def test_ai_trailer_in_pushed_commits_marks_agent(repo):
    repo.write("a.py", "x = 1\n")
    repo.commit("feat: x\n\nCo-Authored-By: Claude <noreply@anthropic.com>")
    res = repo.check("--range", "HEAD~1..HEAD")
    assert res["provenance"]["kind"] == "agent"


# ---------------------------------------------------------------- ranges, hooks and output

def test_range_scans_every_commit_for_secrets(repo):
    repo.write("leak.py", f'K = "{AWS}"\n')
    repo.commit("add")
    repo.write("leak.py", "K = None\n")
    repo.commit("remove")
    res = repo.check("--range", "HEAD~2..HEAD")
    assert find(res, "secrets.hardcoded") and "(commit " in find(res, "secrets.hardcoded")[0]["message"]


def test_installed_hooks_block_and_allow_commits(repo):
    hooks = repo.path / ".git" / "hooks"
    hooks.mkdir(exist_ok=True)
    (hooks / "pre-commit").write_text("#!/bin/sh\necho old-hook-ran\n")
    (hooks / "pre-commit").chmod(0o755)
    assert repo.run("install").returncode == 1                     # refuses to overwrite a foreign hook
    assert repo.run("install", "--force").returncode == 0
    repo.write("ok.py", "def add(a, b):\n    return a + b\n")
    repo.add()
    ok = repo.git("commit", "-m", "clean", check=False)
    assert ok.returncode == 0 and "old-hook-ran" in ok.stdout + ok.stderr
    repo.write("bad.py", f'K = "{AWS}"\n')
    repo.add()
    bad = repo.git("commit", "-m", "leak", check=False)
    assert bad.returncode != 0 and "secrets.hardcoded" in bad.stdout + bad.stderr
    assert repo.run("uninstall").returncode == 0
    assert "old-hook-ran" in (hooks / "pre-commit").read_text()


def test_pre_push_hook_reads_refs_from_stdin(repo):
    repo.write("leak.py", f'K = "{AWS}"\n')
    repo.commit("leak")
    repo.write("leak.py", "K = None\n")
    repo.commit("cleanup")
    head = repo.git("rev-parse", "HEAD").stdout.strip()
    zero = "0" * 40
    proc = repo.run("check", "--push", "--format", "json", input=f"refs/heads/main {head} refs/heads/main {zero}\n")
    data = json.loads(proc.stdout)
    assert proc.returncode == 1 and find(data, "secrets.hardcoded")


def test_vendored_install_and_init(repo):
    assert repo.run("install", "--vendor").returncode == 0
    assert (repo.path / ".aigate" / "aigate_lite.py").exists()
    assert '.aigate/aigate_lite.py' in (repo.path / ".git" / "hooks" / "pre-commit").read_text()
    assert repo.run("init").returncode == 0 and repo.run("init").returncode == 1
    repo.write("ok.py", "x = 1\n")
    repo.add()
    assert repo.git("commit", "-m", "with vendored gate", check=False).returncode == 0   # gate's own file is ignored


def test_github_and_text_output(repo):
    repo.write("a.py", "eval(x)\n")
    repo.add()
    gh = repo.run("check", "--format", "github")
    assert gh.returncode == 1 and "::error file=a.py,line=1,title=security.eval::" in gh.stdout
    text = repo.run("check", "--color", "never")
    assert "FIX" in text.stdout and "commit stopped" in text.stdout


def test_nothing_staged_and_skip(repo):
    proc = repo.run("check")
    assert proc.returncode == 0 and "nothing staged to check" in proc.stdout
    repo.write("a.py", "eval(x)\n")
    repo.add()
    assert repo.run("check", env={"AIGATE_SKIP": "1"}).returncode == 0
    assert repo.run("check", env={"AIGATE_SKIP": "1", "CLAUDECODE": "1"}).returncode == 1


def test_explain(repo):
    assert "secrets.hardcoded" in repo.run("explain").stdout
    out = repo.run("explain", "tests.deleted").stdout
    assert "AIGATE_ALLOW=tests.deleted" in out and "[[allow]]" in out


def test_configured_command_runs(repo):
    repo.write(".aigate/lite.toml", f'[[commands]]\nname = "tests"\nrun = \'{sys.executable} -c "raise SystemExit(3)"\'\n'
                                    'stage = "commit"\n')
    repo.commit()
    assert repo.run("trust").returncode == 0
    repo.write("a.py", "x = 1\n")
    repo.add()
    res = repo.check()
    assert "exited with code 3" in find(res, "commands.failed")[0]["message"]
    assert not find(repo.check("--no-commands"), "commands.failed")


# ---------------------------------------------------------------- helpers

def test_version_ranges():
    assert aigate.npm_spec_allows("^1.2.3", "1.9.0") and not aigate.npm_spec_allows("^1.2.3", "2.0.0")
    assert aigate.npm_spec_allows("^0.2.3", "0.2.9") and not aigate.npm_spec_allows("^0.2.3", "0.3.0")
    assert aigate.npm_spec_allows("~1.2", "1.2.7") and not aigate.npm_spec_allows("~1.2", "1.3.0")
    assert aigate.npm_spec_allows(">=1 <3 || 5.x", "5.1.0") and not aigate.npm_spec_allows(">=1 <3", "3.0.0")
    assert aigate.py_spec_allows(">=2.0,<3", "2.31.0") and not aigate.py_spec_allows("~=2.31", "3.0")
    assert aigate.py_spec_allows("==2.*", "2.5") and aigate.py_spec_allows("^1.2", "1.3") is None


def test_small_helpers():
    assert aigate.edit_distance("reqeusts", "requests", 2) == 1
    assert aigate.path_matches("a/b/tests/x.py", ["**/tests/**"]) and aigate.path_matches("x/key.pem", ["*.pem"])
    assert not aigate.path_matches("src/app.py", ["tests/**"])
    deps, sources = aigate.parse_requirements("flask[async]>=3 ; python_version>'3.8'  # web\n-i https://x/simple\n"
                                              "-e git+https://github.com/a/b.git#egg=b\n", "requirements.txt")
    assert deps["flask"].spec == ">=3" and deps["@git+https://github.com/a/b.git#egg=b"].source == "git"
    assert sources == [(2, "index", "https://x/simple")]
    assert aigate.safe_url("https://user:pass@host/simple") == "https://***@host/simple"
    assert aigate.js_package("@scope/pkg/sub") == "@scope/pkg" and aigate.js_package("./x") is None
    assert aigate.js_package("npm:express@4") == "express"
