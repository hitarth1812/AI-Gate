import json

from conftest import GitRepo, aigate, find

AWS = "AKIA" + "qwertyuiopasdfgh".upper()                 # split so this file is not itself a finding


def fresh(tmp_path, name="repo"):
    (tmp_path / name).mkdir()
    return GitRepo(tmp_path / name, tmp_path / "cache")


def full(repo, *args):
    proc = repo.run("check", "--all", "--format", "json", *args)
    assert proc.returncode in (0, 1), proc.stderr + proc.stdout
    data = json.loads(proc.stdout)
    data["code"] = proc.returncode
    return data


def test_secret_in_the_root_commit_is_found_only_by_all(tmp_path):
    repo = fresh(tmp_path)
    repo.write("config/keys.py", f'K = "{AWS}"\n')
    repo.commit("root")
    repo.write("README.md", "# demo\n")
    repo.commit("second")
    root = repo.git("rev-list", "--max-parents=0", "HEAD").stdout.strip()
    assert not find(repo.check("--range", f"{root}..HEAD"), "secrets.hardcoded")   # the known gap
    res = full(repo)
    hits = find(res, "secrets.hardcoded")
    assert res["code"] == 1 and [f["path"] for f in hits] == ["config/keys.py"] and AWS not in json.dumps(res)


def test_clean_tree_passes_without_change_only_rules(tmp_path):
    repo = fresh(tmp_path)
    repo.write(".aigate/lite.toml", '[paths]\nforbidden = ["infra/**"]\n[limits]\ndiff_files = 2\n')
    for i in range(5):
        repo.write(f"src/m{i}.py", f"def f{i}(a, b):\n    return a + b + {i}\n")
    repo.write("infra/main.tf", 'resource "x" "y" {}\n')
    repo.commit("init")
    res = full(repo)
    assert res["code"] == 0 and res["label"] == "full tree"
    assert not {f["rule"] for f in res["findings"]} & {"size.diff_budget", "scope.forbidden_path",
                                                       "gate.config_changed"}


def test_ignored_paths_are_skipped_and_odd_paths_work(tmp_path):
    repo = fresh(tmp_path)
    repo.write("node_modules/pkg/index.js", f'const k = "{AWS}";\n')
    repo.write("my dir/ünï.py", "x = eval(y)\r\n")
    repo.commit("init")
    res = full(repo)
    assert not find(res, "secrets.hardcoded")
    assert [f["path"] for f in find(res, "security.eval")] == ["my dir/ünï.py"]


def test_all_needs_a_commit_and_is_not_a_hook_mode(tmp_path, repo):
    empty = fresh(tmp_path, "empty")
    proc = empty.run("check", "--all")
    assert proc.returncode == 2 and "at least one commit" in proc.stderr
    assert repo.run("check", "--all", "--staged").returncode == 2
    assert repo.run("check", "--all", "--push").returncode == 2
    for kind in ("pre-commit", "pre-push"):
        for vendored in (True, False):
            assert "--all" not in aigate.hook_script(kind, vendored, "python", "x.py")
