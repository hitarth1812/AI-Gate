import json

from conftest import aigate

make = aigate.make
AWS_LIKE = "AKIA" + "QWERTYUIOPASDFGH"


def result(findings, verdict, risk):
    stats = {"files": 2, "added": 12, "removed": 3, "seconds": 0.1}
    return aigate.Result(findings, verdict, False, risk, ("agent", "$CLAUDECODE"), "staged changes", stats,
                         "built-in defaults", [])


GOLDEN = """\
AIGATE verdict=block risk=100 exit=1
BLOCK secrets.hardcoded cfg/keys.py:1 AWS access key ID (AKIA...FGH) | fix: Remove it and rotate the credential (anything committed is leaked), then load it from an environment variable or a secret manager.
FIX security.eval app/a.py:7 eval() runs code built at runtime | fix: Avoid eval/exec; parse the input explicitly (ast.literal_eval, JSON.parse).
WARN code.todo app/a.py:9 # TODO later -> soon | fix: Finish it or track it in an issue.
WARN secrets.env_file web/.env.production web/.env.production is staged | fix: Unstage it (git rm --cached <file>), add it to .gitignore, and commit a .env.example with placeholder values instead.
END
"""


def golden_result():
    allowed = make("security.shell", "app/b.py", 3, "os.system(...) runs through a shell")
    allowed.allowed = "baseline"
    findings = [make("secrets.hardcoded", "cfg/keys.py", 1, "AWS access key ID (AKIA…FGH)"),
                make("security.eval", "app/a.py", 7, "eval() runs code built at runtime"),
                make("code.todo", "app/a.py", 9, "# TODO later → soon"),
                make("secrets.env_file", "web/.env.production", None, "web/.env.production is staged", action="warn"),
                allowed]
    return result(findings, "block", 100)


def test_golden_agent_report():
    text = aigate.render_agent(golden_result())
    assert text == GOLDEN
    lines = text.splitlines()
    assert lines[0] == "AIGATE verdict=block risk=100 exit=1" and lines[-1] == "END"
    assert [ln.split()[0] for ln in lines[1:-1]] == ["BLOCK", "FIX", "WARN", "WARN"]
    assert "security.shell" not in text                                  # allowed/baseline findings are left out
    assert "AKIA...FGH" in lines[1] and lines[2].startswith("FIX security.eval app/a.py:7 eval() runs code")


def test_ascii_only_and_no_colour():
    text = aigate.render_agent(golden_result())
    text.encode("ascii")
    assert "\x1b" not in text and "->" in text


def test_caps_and_more_line():
    findings = [make("security.eval", f"src/f{i:03d}.py", 1, "eval() runs code") for i in range(60)]
    findings += [make("code.todo", f"src/t{i:03d}.py", 1, "# TODO") for i in range(30)]
    lines = aigate.render_agent(result(findings, "fix", 100)).splitlines()
    assert sum(ln.startswith("FIX ") for ln in lines) == 50 and sum(ln.startswith("WARN ") for ln in lines) == 20
    assert lines[-2] == "MORE 20" and lines[-1] == "END"


def test_order_is_stable_across_runs_and_inputs():
    a = golden_result()
    b = golden_result()
    b.findings.reverse()
    assert aigate.render_agent(a) == aigate.render_agent(b)


def test_pass_has_only_the_header_and_end():
    assert aigate.render_agent(result([], "pass", 0)) == "AIGATE verdict=pass risk=0 exit=0\nEND\n"


def test_cli_format_agent_end_to_end(repo):
    repo.write("a.py", "x = eval(y)\n")
    repo.write("keys.py", f'K = "{AWS_LIKE}"\n')
    repo.add()
    proc = repo.run("check", "--format", "agent", env={"PYTHONIOENCODING": "cp1252"})
    lines = proc.stdout.splitlines()
    assert proc.returncode == 1 and lines[0].startswith("AIGATE verdict=block ") and lines[-1] == "END"
    assert AWS_LIKE not in proc.stdout and all(ord(ch) < 128 for ch in proc.stdout)
    assert json.loads(repo.run("check", "--format", "json").stdout)["verdict"] == "block"   # json unchanged
