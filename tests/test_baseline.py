import json
import re

from conftest import aigate, find

AWS = "AKIA" + "QWERTYUIOPASDFGH"                 # split so this file is not itself a finding
IGNORE = "# type" + ": ignore"
CORPUS = {
    "app/risky.py": (
        "import pickle\nimport subprocess\nimport yaml\nimport requests\nimport torch\n"
        "from contextlib import suppress\nfrom markupsafe import Markup\n\n\n"
        "def run(cmd, data, q, conn, app):\n"
        "    eval(cmd)\n"
        "    subprocess.run(cmd, shell=True)\n"
        "    pickle.loads(data)\n"
        "    yaml.load(data)\n"
        "    torch.load(data, weights_only=False)\n"
        "    conn.execute('SELECT * FROM t WHERE id = ' + q)\n"
        "    requests.get('https://example.org', verify=False)\n"
        "    app.run(debug=True)\n"
        "    Markup(q)\n"
        "    breakpoint()\n"
        "    with suppress(Exception):\n"
        "        cmd()\n"
        "    try:\n"
        "        cmd()\n"
        "    except Exception:\n"
        "        pass\n"
        "    try:\n"
        "        cmd()\n"
        "    except:\n"
        "        cmd()\n"
        f"    x = cmd()  {IGNORE}\n"
        "    # TODO later\n"
        "    return x\n"),
    "app/keys.py": f'K = "{AWS}"\n',
    "web/app.js": "describe('a', () => {\n  it.only('b', () => { expect(1).toBe(1) })\n})\n",
}
LINE_KEYED = {"security.eval", "security.shell", "security.deserialization", "security.sql_injection",
              "security.verification_disabled", "security.insecure_default", "security.xss", "code.debug_leftover",
              "code.swallowed_exception", "code.bare_except", "suppress.type", "code.todo", "secrets.hardcoded"}


def full(repo, *args, env=None):
    proc = repo.run("check", "--all", "--format", "json", *args, env=env)
    assert proc.returncode in (0, 1), proc.stderr + proc.stdout
    return {**json.loads(proc.stdout), "code": proc.returncode}


def ids(res):
    return sorted((f["rule"], f["path"], f["fingerprint"]) for f in res["findings"])


def write_corpus(repo):
    for rel, text in CORPUS.items():
        repo.write(rel, text)
    repo.commit("corpus")


def baseline(repo, *args, env=None):
    return repo.run("baseline", *args, env=env)


def baseline_doc(repo):
    return json.loads((repo.path / ".aigate" / "baseline.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------- 4a: stable fingerprints

def test_inserting_lines_above_keeps_every_fingerprint(repo):
    write_corpus(repo)
    before = full(repo)
    assert LINE_KEYED <= {f["rule"] for f in before["findings"]}, "the corpus must trigger every line-keyed rule"
    for rel, text in CORPUS.items():
        pad = "// pad\n" * 5 if rel.endswith(".js") else "# pad\n" * 5
        repo.write(rel, pad + text)
    repo.commit("shifted")
    after = full(repo)
    assert ids(after) == ids(before)
    moved = {f["rule"] for f in before["findings"]} - {"secrets.hardcoded", "code.todo", "suppress.type"}
    legacy_before = {(f["rule"], f["legacy_fingerprint"]) for f in before["findings"] if f["rule"] in moved}
    legacy_after = {(f["rule"], f["legacy_fingerprint"]) for f in after["findings"] if f["rule"] in moved}
    assert legacy_before != legacy_after                        # the 0.1 formula did depend on the line


def test_new_fingerprints_are_16_hex_and_legacy_12():
    f = aigate.make("security.eval", "a.py", 3, "eval() runs code", key="eval:<module>:eval(x)", legacy_key="eval:3")
    assert re.fullmatch(r"[0-9a-f]{16}", f.fingerprint) and re.fullmatch(r"[0-9a-f]{12}", f.legacy_fingerprint)
    assert f.legacy_fingerprint == aigate.fingerprint("security.eval", "a.py", "eval:3")


def test_an_allow_entry_with_a_legacy_fingerprint_still_matches(repo):
    repo.write("a.py", "x = eval(y)\n")
    repo.commit("a")
    hit = find(full(repo), "security.eval")[0]
    repo.write(".aigate/lite.toml", f'[[allow]]\nrule = "security.eval"\nfingerprint = "{hit["legacy_fingerprint"]}"\n'
                                    'reason = "test"\n')
    repo.commit("allow")
    allowed = find(full(repo), "security.eval")[0]["allowed"]
    assert allowed and hit["fingerprint"] in allowed          # tells you the new fingerprint to use


def test_twins_in_one_file_get_distinct_fingerprints(repo):
    repo.write("a.py", "def f(c):\n    try:\n        c()\n    except Exception:\n        pass\n"
                       "    try:\n        c()\n    except Exception:\n        pass\n")
    repo.commit("twins")
    fps = [f["fingerprint"] for f in find(full(repo), "code.swallowed_exception")]
    assert len(fps) == 2 and len(set(fps)) == 2


# ---------------------------------------------------------------- 4b: baseline

def test_baseline_lets_an_old_repo_pass_and_a_new_finding_still_fails(repo):
    write_corpus(repo)
    proc = baseline(repo)
    assert proc.returncode == 0 and "Wrote .aigate/baseline.json" in proc.stdout
    assert proc.stdout.splitlines()[-1].startswith("next: ")
    repo.commit("baseline")
    res = full(repo)
    assert find(res, "secrets.hardcoded") and res["verdict"] == "block"            # secrets are never baselined
    assert all(f["allowed"] == "baseline" for f in res["findings"] if not f["rule"].startswith("secrets."))
    repo.write("app/new.py", "y = eval(z)\n")
    repo.add()
    assert {f["action"] for f in find(repo.check(), "security.eval") if not f["allowed"]} == {"fix"}


def test_line_shift_still_matches_the_baseline(repo):
    write_corpus(repo)
    baseline(repo)
    repo.write("app/risky.py", "# moved\n" * 7 + CORPUS["app/risky.py"])
    repo.commit("shift")
    loud = [f for f in full(repo)["findings"] if not f["allowed"] and not f["rule"].startswith("secrets.")]
    assert loud == []


def test_secrets_are_excluded_unless_asked_and_always_reported(repo):
    write_corpus(repo)
    out = baseline(repo).stdout
    assert "1 secret finding not baselined; rotate it" in out
    assert not [e for e in baseline_doc(repo)["entries"] if e["rule"].startswith("secrets.")]
    baseline(repo, "--include-secrets")
    repo.commit("bl")
    assert full(repo)["verdict"] in ("pass", "warn")


def test_prune_drops_entries_that_no_longer_occur(repo):
    write_corpus(repo)
    repo.write("app/old.py", "y = eval(z)\n")
    repo.commit("old")
    baseline(repo)
    n = len(baseline_doc(repo)["entries"])
    (repo.path / "app" / "old.py").unlink()
    repo.commit("drop old")
    keep = baseline(repo)
    assert len(baseline_doc(repo)["entries"]) == n and "0 new" in keep.stdout
    pruned = baseline(repo, "--prune")
    assert len(baseline_doc(repo)["entries"]) == n - 1 and "1 removed" in pruned.stdout


def test_baseline_file_is_deterministic_and_has_no_code_or_secrets(repo):
    write_corpus(repo)
    baseline(repo, "--include-secrets")
    first = (repo.path / ".aigate" / "baseline.json").read_bytes()
    baseline(repo, "--include-secrets")
    assert (repo.path / ".aigate" / "baseline.json").read_bytes() == first
    doc = json.loads(first)
    assert set(doc) == {"version", "tool", "entries"} and all(set(e) == {"rule", "path", "fingerprint"}
                                                               for e in doc["entries"])
    assert [(e["path"], e["rule"], e["fingerprint"]) for e in doc["entries"]] == \
        sorted((e["path"], e["rule"], e["fingerprint"]) for e in doc["entries"])
    text = first.decode()
    assert AWS not in text and "eval(" not in text and "\r" not in text
    for _label, rx in aigate.PROVIDER_SECRETS:
        assert not rx.search(text)


def test_an_agent_cannot_write_a_baseline_and_reads_it_from_head(repo):
    write_corpus(repo)
    refused = baseline(repo, env={"CLAUDECODE": "1"})
    assert refused.returncode == 1 and "AI agent" in refused.stderr
    assert not (repo.path / ".aigate" / "baseline.json").exists()
    baseline(repo)                                                    # a person accepts today's findings
    repo.commit("baseline")
    repo.write("app/new.py", "y = eval(z)\n")
    repo.add()
    doc = baseline_doc(repo)                                          # the agent tampers with the worktree copy
    staged = repo.check()
    hit = find(staged, "security.eval")[0]
    doc["entries"].append({"rule": "security.eval", "path": "app/new.py", "fingerprint": hit["fingerprint"]})
    (repo.path / ".aigate" / "baseline.json").write_text(json.dumps(doc), encoding="utf-8")
    assert find(repo.check(), "security.eval")[0]["allowed"] is None       # unstaged edits are never read
    agent = repo.check(env={"CLAUDECODE": "1"})
    assert find(agent, "security.eval")[0]["allowed"] is None and agent["verdict"] in ("fix", "block")
    repo.add()
    staged = repo.check()                          # even staged: outside --all a baseline never accepts new code
    assert find(staged, "security.eval")[0]["allowed"] is None and any("differs from HEAD" in n for n in staged["notes"])


def risky_with(extra_after, text, line="    breakpoint()\n"):
    return CORPUS["app/risky.py"].replace(line, line + extra_after) if extra_after else text


def test_a_new_copy_of_a_baselined_finding_is_reported_in_staged_and_range_mode(repo):
    write_corpus(repo)
    baseline(repo)
    repo.commit("baseline")
    copies = "    eval(cmd)\n    try:\n        cmd()\n    except Exception:\n        pass\n    # TODO later\n"
    repo.write("app/risky.py", risky_with(copies, ""))
    repo.add()
    for env in (None, {"CLAUDECODE": "1"}):
        res = repo.check(env=env)
        loud = {f["rule"] for f in res["findings"] if not f["allowed"]}
        assert {"security.eval", "code.swallowed_exception", "code.todo"} <= loud, env
    repo.commit("copies")
    ranged = repo.check("--range", "HEAD~1..HEAD")
    assert {"security.eval", "code.swallowed_exception"} <= {f["rule"] for f in ranged["findings"] if not f["allowed"]}


def test_an_edited_line_keeps_its_baselined_debt(repo):
    write_corpus(repo)
    baseline(repo)
    repo.commit("baseline")
    repo.write("app/risky.py", CORPUS["app/risky.py"].replace("    eval(cmd)\n", "    eval(cmd)  # reviewed 2026\n"))
    repo.add()
    hits = find(repo.check(), "security.eval")
    assert hits and all(f["allowed"] == "baseline" for f in hits)


def test_a_legacy_allow_entry_covers_only_the_first_twin(repo):
    two = f"a = f()  {IGNORE}\nb = g()  {IGNORE}\n"
    legacy = aigate.fingerprint("suppress.type", "app/two.py", IGNORE)     # the 0.1 key was the marker alone
    repo.write(".aigate/lite.toml", f'[[allow]]\nrule = "suppress.type"\nfingerprint = "{legacy}"\nreason = "old"\n')
    repo.commit("allow")
    repo.write("app/two.py", two)
    repo.add()
    hits = sorted(find(repo.check(), "suppress.type"), key=lambda f: f["line"])
    assert [bool(f["allowed"]) for f in hits] == [True, False]


def test_baseline_loader_accepts_a_bom_and_rejects_bad_versions_and_gate_rules(repo):
    repo.write("a.py", "x = eval(y)\n")
    repo.commit("a")
    fp = find(full(repo), "security.eval")[0]["fingerprint"]
    entries = [{"rule": "security.eval", "path": "a.py", "fingerprint": fp},
               {"rule": "gate.error", "path": "(gate)", "fingerprint": "0" * 16}]
    path = repo.path / ".aigate" / "baseline.json"
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(b"\xef\xbb\xbf" + json.dumps({"version": 1, "entries": entries}).encode())
    repo.commit("bom")
    repo_obj = aigate.Repo.discover(str(repo.path))
    assert aigate.load_baseline(repo_obj, "worktree")[0] == {("security.eval", "a.py", fp)}
    assert find(full(repo), "security.eval")[0]["allowed"] == "baseline"
    path.write_text(json.dumps({"version": True, "entries": entries}), encoding="utf-8")
    assert aigate.load_baseline(repo_obj, "worktree")[0] == set()


def test_text_output_hides_baseline_hits_and_verbose_shows_them(repo):
    write_corpus(repo)
    baseline(repo, "--include-secrets")
    repo.commit("bl")
    quiet = repo.run("check", "--all", "--color", "never").stdout
    loud = repo.run("check", "--all", "--color", "never", "-v").stdout
    assert "baseline findings hidden)" in quiet and "BASELINE" not in quiet and "BASELINE" in loud


def test_corrupt_baseline_is_ignored_with_a_note(repo):
    repo.write("a.py", "x = eval(y)\n")
    repo.write(".aigate/baseline.json", "{not json")
    repo.commit("c")
    res = full(repo)
    assert find(res, "security.eval")[0]["allowed"] is None and any("baseline" in n for n in res["notes"])


def test_doctor_reports_the_baseline_and_legacy_entries(repo):
    write_corpus(repo)
    baseline(repo)
    checks = {c["id"]: c for c in json.loads(repo.run("doctor", "--offline", "--format", "json").stdout)}
    assert checks["baseline"]["status"] == "ok" and "accepted findings" in checks["baseline"]["detail"]
    doc = baseline_doc(repo)
    doc["entries"][0]["fingerprint"] = "0123456789ab"                  # a 0.1-format entry
    (repo.path / ".aigate" / "baseline.json").write_text(json.dumps(doc), encoding="utf-8")
    checks = {c["id"]: c for c in json.loads(repo.run("doctor", "--offline", "--format", "json").stdout)}
    assert checks["baseline"]["status"] == "warn" and "--prune" in checks["baseline"]["fix"]
