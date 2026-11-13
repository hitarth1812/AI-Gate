"""The `next:` line of a BLOCK verdict is true for the findings that block: only a secrets.* BLOCK talks about secrets."""
import pytest

from conftest import aigate

make = aigate.make
AWS = "AWS access key ID (AKIA…FGH)"


def block(rule, path="a.py", msg="x"):
    f = make(rule, path, 1, msg)
    f.action = "block"
    return f


def next_line(findings, *, stage="commit", agent=False):
    prov = ("agent", "$CLAUDECODE") if agent else ("human", "default")
    stats = {"files": 1, "added": 1, "removed": 0, "seconds": 0.1}
    res = aigate.Result(findings, "block", False, 50, prov, "staged changes", stats, "built-in defaults", [])
    lines = [ln for ln in aigate.render_text(res, stage=stage).splitlines() if ln.startswith("next: ")]
    assert len(lines) == 1
    return lines[0]


STAGES = pytest.mark.parametrize("stage", ["commit", "push", "scan"])
WHO = pytest.mark.parametrize("agent", [False, True])


@STAGES
@WHO
def test_suppress_security_block_never_mentions_secrets(stage, agent):
    line = next_line([block("suppress.security", msg="`# nosec` added")], stage=stage, agent=agent)
    assert "secret" not in line.lower()
    assert "suppression" in line and "[[allow]]" in line and ".aigate/lite.toml" in line
    assert line.endswith("aigate-lite explain suppress.security") or "aigate-lite explain suppress.security" in line


@STAGES
@WHO
def test_hallucinated_dependency_block_never_mentions_secrets(stage, agent):
    line = next_line([block("deps.hallucinated", "requirements.txt", "fakepkg does not exist on PyPI")],
                     stage=stage, agent=agent)
    assert "secret" not in line.lower()
    assert "[[allow]]" in line and "aigate-lite explain deps.hallucinated" in line


@STAGES
def test_forbidden_path_block_is_not_offered_a_baseline(stage):
    line = next_line([block("scope.forbidden_path", "infra/x.tf", "forbidden")], stage=stage)
    assert "secret" not in line.lower() and "aigate-lite baseline" not in line   # scope.* is never baselined
    assert "aigate-lite explain scope.forbidden_path" in line


@STAGES
@WHO
def test_secret_block_keeps_todays_wording(stage, agent):
    line = next_line([block("secrets.hardcoded", "cfg/keys.py", AWS)], stage=stage, agent=agent)
    if stage == "scan":
        assert line == ("next: fix the 1 BLOCK line above (secrets cannot be baselined), then adopt a baseline so only "
                        "new findings count: aigate-lite baseline")
    else:
        assert line == "next: fix the 1 BLOCK line above"


@STAGES
@WHO
def test_mixed_blocks_do_not_call_a_non_secret_a_secret(stage, agent):
    line = next_line([block("secrets.hardcoded", "cfg/keys.py", AWS), block("suppress.security", msg="`# nosec` added")],
                     stage=stage, agent=agent)
    assert "2 BLOCK lines" in line
    assert "cannot be baselined)" not in line                     # the blanket claim about "the BLOCK lines"
    assert "suppression" in line and "secret" in line.lower()
    assert "aigate-lite explain" in line


def test_the_advice_is_the_same_for_a_person_and_an_agent():
    for rule in ("suppress.security", "deps.hallucinated", "scope.forbidden_path"):
        for stage in ("commit", "push", "scan"):
            assert next_line([block(rule)], stage=stage) == next_line([block(rule)], stage=stage, agent=True)
    assert "AIGATE_ALLOW" not in next_line([block("suppress.security")])      # AIGATE_ALLOW never applies to a BLOCK


def test_end_to_end_audit_of_a_bare_nosec(repo):
    repo.write("auth/a.py", "x = 1  # nosec\n")
    repo.commit("init")
    out = repo.run("check", "--all", "--color", "never").stdout
    nxt = [ln for ln in out.splitlines() if ln.startswith("next: ")]
    assert "suppress.security" in out and "BLOCK" in out
    assert len(nxt) == 1 and "secret" not in nxt[0].lower() and "aigate-lite explain suppress.security" in nxt[0]
