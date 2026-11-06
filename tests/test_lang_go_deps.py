"""Go modules: go.mod / go.work parsing, registry lookups (fixtures only), imports versus go.mod, GOPRIVATE."""
import json
import urllib.error

from conftest import aigate, find

BASE_MOD = "module example.com/app\n\ngo 1.21\n\nrequire github.com/pkg/errors v0.9.1\n"
BASE_GO = 'package main\n\nimport "github.com/pkg/errors"\n\nfunc main() { _ = errors.New("x") }\n'


def project(repo, mod=BASE_MOD, extra=None):
    repo.write("go.mod", mod)
    repo.write("main.go", BASE_GO)
    for path, text in (extra or {}).items():
        repo.write(path, text)
    repo.commit("base")


def change(repo, files, *args, env=None):
    for path, text in files.items():
        if text is None:
            (repo.path / path).unlink()
        else:
            repo.write(path, text)
    repo.add()
    return repo.check(*args, env=env)


def messages(res, rule):
    return [f["message"] for f in find(res, rule) if not f["allowed"]]


# ---------------------------------------------------------------- the parser

def test_parse_go_mod_blocks_comments_quotes_and_indirect():
    text = ('module "example.com/app"\n\ngo 1.21\ntoolchain go1.22.1\n\nrequire (\n\tgithub.com/a/b v1.2.3\n'
            '\tgithub.com/c/d v0.0.0-20210101000000-abcdef123456 // indirect\n\t// a comment line\n)\n'
            'require github.com/e/f v2.0.0+incompatible\nexclude github.com/x/y v1.0.0\nretract [v1.0.0, v1.1.0]\n')
    deps, problems, module = aigate.parse_go_mod(text, "go.mod")
    assert module == "example.com/app" and problems == []
    assert {k: (d.spec, d.indirect) for k, d in deps.items()} == {
        "github.com/a/b": ("v1.2.3", False),
        "github.com/c/d": ("v0.0.0-20210101000000-abcdef123456", True),
        "github.com/e/f": ("v2.0.0+incompatible", False)}
    assert deps["github.com/a/b"].line == 7 and deps["github.com/a/b"].eco == "go"


def test_parse_go_mod_replace_forms():
    text = ("replace github.com/a/b => ../local/b\nreplace github.com/c/d v1.0.0 => github.com/fork/d v1.0.1\n"
            "replace (\n\tgithub.com/e/f => ./vendor/f\n\tgithub.com/g/h => C:\\src\\h\n)\n")
    deps, problems, _m = aigate.parse_go_mod(text, "go.mod")
    assert problems == []
    assert [(d.name, d.source, d.spec) for d in deps.values()] == [
        ("github.com/a/b", "path", "../local/b"), ("github.com/c/d", "replace", "github.com/fork/d"),
        ("github.com/e/f", "path", "./vendor/f"), ("github.com/g/h", "path", "C:\\src\\h")]


def test_parse_go_work_use_and_crlf():
    deps, problems, _m = aigate.parse_go_mod("go 1.21\r\n\r\nuse (\r\n\t./a\r\n\t./b\r\n)\r\nuse ./c\r\n", "go.work")
    assert deps == {} and problems == []
    deps, problems, module = aigate.parse_go_mod("module x.io/m\r\nrequire github.com/a/b v1.0.0\r\n", "go.mod")
    assert module == "x.io/m" and deps["github.com/a/b"].spec == "v1.0.0"


def test_malformed_lines_are_reported_not_ignored():
    for bad in ("require github.com/a/b\n", "frobnicate x y\n", "replace a => \n", "require (\n\tgithub.com/a/b v1.0.0\n",
                "module\n"):
        _deps, problems, _m = aigate.parse_go_mod(bad, "go.mod")
        assert problems, bad


# ---------------------------------------------------------------- go.mod findings through the gate

def test_malformed_go_mod_lines_become_unverified_findings(repo):
    project(repo)
    res = change(repo, {"go.mod": BASE_MOD + "frobnicate github.com/x\nrequire github.com/oops\n"})
    found = messages(res, "deps.unverified")
    assert len(found) == 2 and all("could not be parsed" in m for m in found)
    assert find(res, "deps.unverified")[0]["action"] == "warn"


def test_new_requirements_are_checked_against_the_proxy_fixture(repo):
    project(repo)
    res = change(repo, {"go.mod": BASE_MOD + "require (\n\tgithub.com/new/shiny v0.1.0\n\tgithub.com/not/real v1.0.0\n"
                                             "\tgithub.com/gin-gonic/gin v1.10.0\n\tgithub.com/pkg/errors v0.9.9\n)\n"
                                             .replace("require github.com/pkg/errors v0.9.1\n", "")})
    assert [f["message"] for f in find(res, "deps.new_package")] == ["github.com/new/shiny was first published 6 days ago"]
    assert any("github.com/not/real does not exist" in m for m in messages(res, "deps.hallucinated"))
    assert any("no version v0.9.9" in m for m in messages(res, "deps.hallucinated"))
    assert any("github.com/gin-gonic/gin v1.10.0 was published" in m for m in messages(res, "deps.version_cooldown"))


def test_vulnerable_and_clean_versions(repo):
    project(repo)
    bad = change(repo, {"go.mod": BASE_MOD + "require github.com/gin-gonic/gin v1.7.0\n"})
    assert any("GO-2021-0001" in m for m in messages(bad, "deps.vulnerable"))
    good = change(repo, {"go.mod": BASE_MOD + "require github.com/gin-gonic/gin v1.9.1\n"})
    assert not find(good, "deps.vulnerable") and not find(good, "deps.hallucinated")


def test_pseudo_versions_exist_even_though_the_proxy_list_omits_them(repo):
    project(repo)
    res = change(repo, {"go.mod": BASE_MOD + "require golang.org/x/text v0.0.0-20170915032832-14c0d48ead0c\n"})
    assert not find(res, "deps.hallucinated")


def test_uppercase_module_paths_resolve_case_sensitively(repo):
    project(repo)
    res = change(repo, {"go.mod": BASE_MOD + "require github.com/BurntSushi/toml v1.3.2\n"})
    assert not find(res, "deps.hallucinated")
    wrong = change(repo, {"go.mod": BASE_MOD + "require github.com/burntsushi/toml v1.3.2\n"})
    assert find(wrong, "deps.hallucinated")


def test_unchanged_requirements_are_not_rechecked(repo):
    project(repo, BASE_MOD + "require github.com/not/real v1.0.0\n")
    res = change(repo, {"go.mod": BASE_MOD + "require github.com/not/real v1.0.0\n// a comment\n"})
    assert not find(res, "deps.hallucinated")


def test_indirect_requirements_must_exist_and_be_clean_but_get_no_age_rules(repo):
    # changed from "only the vulnerability check": a direct, invented dependency must not pass by carrying a
    # `// indirect` comment, so the version has to exist on the proxy. Age rules stay off (go mod tidy adds many).
    project(repo)
    res = change(repo, {"go.mod": BASE_MOD + "require github.com/indirect/dep v1.0.0 // indirect\n"
                                             "require github.com/not/real v1.0.0 // indirect\n"
                                             "require github.com/new/shiny v0.1.0 // indirect\n"})
    assert [f["message"] for f in find(res, "deps.vulnerable")]
    assert [f["message"] for f in find(res, "deps.hallucinated")] == [
        "github.com/not/real has no version v1.0.0 on the Go module proxy (marked // indirect)"]
    assert not find(res, "deps.new_package") and not find(res, "deps.version_cooldown")


def test_a_comment_glued_to_the_version_is_still_a_comment():
    deps, problems, _m = aigate.parse_go_mod("require github.com/a/b v1.0.0//indirect\n", "go.mod")
    assert problems == [] and deps["github.com/a/b"].spec == "v1.0.0" and deps["github.com/a/b"].indirect


def test_replace_lines_for_one_module_with_different_versions_are_all_kept():
    deps, _p, _m = aigate.parse_go_mod("replace c.com/z v1.0.0 => ./l\nreplace c.com/z v2.0.0 => evil.com/z v1.0.0\n", "go.mod")
    assert [(d.source, d.spec) for d in deps.values()] == [("path", "./l"), ("replace", "evil.com/z")]


def test_a_required_module_that_is_replaced_locally_or_is_a_sibling_is_not_looked_up(repo):
    project(repo)
    res = change(repo, {"go.mod": BASE_MOD + "require example.com/mymod v0.0.0-00010101000000-000000000000\n"
                                             "replace example.com/mymod => ../mymod\n"})
    assert not find(res, "deps.hallucinated") and find(res, "deps.local_source")
    repo.git("reset", "-q")
    sibling = change(repo, {"mods/other/go.mod": "module example.com/other\n\ngo 1.21\n",
                            "go.mod": BASE_MOD + "require example.com/other v0.0.0-00010101000000-000000000000\n"})
    assert not find(sibling, "deps.hallucinated")


def test_a_replaced_module_that_is_not_replaced_locally_is_still_reported(repo):
    project(repo)
    res = change(repo, {"go.mod": BASE_MOD + "require github.com/gin-gonic/gin v1.9.1\n"
                                             "replace github.com/gin-gonic/gin => github.com/evil/gin v1.0.0\n"})
    assert find(res, "deps.untrusted_source") and not find(res, "deps.hallucinated")


def test_gopath_style_settings_are_read_from_the_go_env_file(repo, tmp_path):
    project(repo)
    envfile = tmp_path / "goenv"
    envfile.write_text("GOPRIVATE=github.com/corp\n# comment\nGOFLAGS=-mod=mod\n")
    mod = BASE_MOD + "require github.com/corp/internal v1.0.0\n"
    assert find(change(repo, {"go.mod": mod}), "deps.hallucinated")
    assert not find(change(repo, {"go.mod": mod}, env={"GOENV": str(envfile)}), "deps.hallucinated")
    proxy = tmp_path / "goenv2"
    proxy.write_text("GOPROXY=https://proxy.corp.example,https://proxy.golang.org\n")
    res = change(repo, {"go.mod": BASE_MOD + "require github.com/not/real v1.0.0\n"}, env={"GOENV": str(proxy)})
    assert not find(res, "deps.hallucinated")


def test_goproxy_first_entry_decides(monkeypatch):
    for value, want in (("", True), ("https://proxy.golang.org", True), ("https://proxy.golang.org,direct", True),
                        ("direct", False), ("off", False), ("https://corp.example,https://proxy.golang.org", False)):
        monkeypatch.setenv("GOPROXY", value)
        assert aigate.go_registry_enabled() is want, value


def test_candidates_cover_gopkg_in_gitlab_subgroups_and_unknown_hosts():
    c = aigate.go_candidates
    assert c("gopkg.in/natefinch/lumberjack.v2") == ["gopkg.in/natefinch/lumberjack.v2"]
    assert c("gopkg.in/yaml.v3") == ["gopkg.in/yaml.v3"]
    assert c("gitlab.com/a/b/c/d/e") == ["gitlab.com/a/b/c/d/e", "gitlab.com/a/b/c/d", "gitlab.com/a/b/c", "gitlab.com/a/b",
                                         "gitlab.com/a"][:5]
    assert c("git.corp.com/g/s/repo/pkg") == ["git.corp.com/g/s/repo/pkg", "git.corp.com/g/s/repo", "git.corp.com/g/s",
                                              "git.corp.com/g"]
    assert c("github.com/a") == []


def test_an_unknown_layout_import_with_no_module_is_unverified_not_a_block(repo):
    project(repo)
    res = change(repo, {"main.go": 'package main\n\nimport "git.corp.com/g/s/repo/pkg"\n'})
    assert find(res, "deps.unverified") and not find(res, "deps.hallucinated")
    short = change(repo, {"main.go": 'package main\n\nimport "github.com/a"\n'})
    assert not find(short, "deps.hallucinated")


def test_testdata_imports_are_fixtures(repo):
    project(repo)
    res = change(repo, {"testdata/x/main.go": 'package main\n\nimport "github.com/not/real/pkg"\n'})
    assert not find(res, "deps.hallucinated")


def test_unexpected_response_shapes_are_unknown_answers_not_crashes(monkeypatch):
    reg, _p = registry(monkeypatch, {f"{P}/github.com/a/b/@v/v1.0.0.info": (200, b"[]"),
                                     f"{P}/github.com/a/b/@v/v2.0.0.info": (200, b'{"Time": 5}'),
                                     f"{P}/github.com/a/b/@v/list": (200, b"v1.0.0\n"),
                                     f"{P}/github.com/a/c/@v/list": (200, b"{}")})
    assert reg.version_time("go", "github.com/a/b", "v1.0.0") is None and reg.failures == 1
    assert reg.version_time("go", "github.com/a/b", "v2.0.0") is None
    assert isinstance(reg.info("go", "github.com/a/b"), dict) or reg.info("go", "github.com/a/b") is None


def test_osv_batch_tolerates_odd_result_shapes(monkeypatch):
    answer = {"results": [{"vulns": "nope"}, None, {"vulns": [{"id": ["x"]}, {"id": "GO-7"}, 5]}]}
    reg, _p = registry(monkeypatch, {"https://api.osv.dev/v1/querybatch": (200, json.dumps(answer).encode())})
    keys = [("osv", "go", "a/b", "v1"), ("osv", "go", "c/d", "v1"), ("osv", "go", "e/f", "v1")]
    reg.prefetch(keys)
    assert [reg.vulns("go", k[2], "v1") for k in keys] == [[], [], ["GO-7"]]


def test_an_unreachable_proxy_leaves_requirements_unverified_not_silently_fine(repo):
    project(repo)
    res = change(repo, {"go.mod": BASE_MOD + "require github.com/offline/mod v1.0.0\n"})
    assert any("github.com/offline/mod" in m for m in messages(res, "deps.unverified"))
    assert not find(res, "deps.hallucinated")


def test_replace_directives(repo):
    project(repo)
    res = change(repo, {"go.mod": BASE_MOD + "replace github.com/pkg/errors => ../errors\n"
                                             "replace github.com/gin-gonic/gin => github.com/evil/gin v1.0.0\n"})
    assert any("local path ../errors" in m for m in messages(res, "deps.local_source"))
    assert find(res, "deps.untrusted_source")[0]["action"] == "block"
    assert "github.com/evil/gin" in find(res, "deps.untrusted_source")[0]["message"]


def test_go_work_replace_and_use(repo):
    project(repo)
    res = change(repo, {"go.work": "go 1.21\n\nuse (\n\t.\n\t./tools\n)\n\nreplace github.com/pkg/errors => ../e\n"})
    assert len(find(res, "deps.local_source")) == 1 and not find(res, "deps.untrusted_source")


def test_private_modules_are_not_looked_up(repo):
    project(repo)
    mod = BASE_MOD + "require github.com/corp/internal v1.0.0\n"
    assert find(change(repo, {"go.mod": mod}), "deps.hallucinated")
    repo.git("reset", "-q")
    repo.write("go.mod", BASE_MOD)
    cfg = change(repo, {".aigate/lite.toml": '[deps]\nprivate = ["github.com/corp/*"]\n', "go.mod": mod})
    assert not find(cfg, "deps.hallucinated")


def test_goprivate_and_gonoproxy_environment_patterns(repo):
    project(repo)
    mod = BASE_MOD + "require github.com/corp/internal v1.0.0\n"
    for var, value in (("GOPRIVATE", "github.com/corp"), ("GONOPROXY", "*.example.com,github.com/corp/*")):
        res = change(repo, {"go.mod": mod}, env={var: value})
        assert not find(res, "deps.hallucinated"), var
    other = change(repo, {"go.mod": mod}, env={"GOPRIVATE": "github.com/other"})
    assert find(other, "deps.hallucinated")


def test_a_non_default_goproxy_skips_the_public_proxy_checks(repo):
    project(repo)
    res = change(repo, {"go.mod": BASE_MOD + "require github.com/not/real v1.0.0\n"},
                 env={"GOPROXY": "https://proxy.corp.example/,direct"})
    assert not find(res, "deps.hallucinated")


def test_crlf_go_mod(repo):
    project(repo)
    (repo.path / "go.mod").write_bytes((BASE_MOD + "require github.com/not/real v1.0.0\n").replace("\n", "\r\n").encode())
    repo.add()
    assert find(repo.check(), "deps.hallucinated")


# ---------------------------------------------------------------- imports versus go.mod

def test_a_new_import_of_an_undeclared_module(repo):
    project(repo)
    res = change(repo, {"main.go": BASE_GO.replace('import "github.com/pkg/errors"',
                                                   'import (\n\t"fmt"\n\t"github.com/pkg/errors"\n\t"github.com/gin-gonic/gin/binding"\n)')})
    found = find(res, "deps.undeclared")
    assert [f["line"] for f in found] == [6] and "github.com/gin-gonic/gin" in found[0]["message"]
    assert found[0]["action"] == "fix"


def test_declared_stdlib_local_and_subpackage_imports_are_fine(repo):
    project(repo, extra={"util/u.go": "package util\n"})
    res = change(repo, {"main.go": 'package main\n\nimport (\n\t"fmt"\n\t"net/http"\n\t"example.com/app/util"\n'
                                   '\terrors "github.com/pkg/errors"\n\t_ "github.com/pkg/errors/sub"\n\t"C"\n)\n'})
    assert not find(res, "deps.undeclared") and not find(res, "deps.hallucinated")


def test_import_of_a_module_that_does_not_exist_is_hallucinated(repo):
    project(repo)
    res = change(repo, {"main.go": 'package main\n\nimport "github.com/not/real/pkg"\n'})
    assert find(res, "deps.hallucinated") and "github.com/not/real/pkg" in find(res, "deps.hallucinated")[0]["message"]
    assert find(res, "deps.hallucinated")[0]["action"] == "block"


def test_versioned_module_paths_are_found(repo):
    project(repo)
    res = change(repo, {"main.go": 'package main\n\nimport "github.com/go-chi/chi/v5/middleware"\n'})
    assert find(res, "deps.undeclared") and "chi/v5" in find(res, "deps.undeclared")[0]["message"]


def test_imports_in_comments_strings_and_unchanged_lines_are_ignored(repo):
    project(repo)
    res = change(repo, {"main.go": BASE_GO + '\n// import "github.com/gin-gonic/gin"\nvar s = `\nimport "github.com/gin-gonic/gin"\n`\n'})
    assert not find(res, "deps.undeclared")


def test_no_go_mod_means_nothing_to_compare_with(repo):
    res = change(repo, {"main.go": 'package main\n\nimport "github.com/gin-gonic/gin"\n'})
    assert not find(res, "deps.undeclared")


def test_private_imports_and_first_party_config_are_skipped(repo):
    project(repo)
    files = {"main.go": 'package main\n\nimport (\n\t"github.com/corp/internal/x"\n\t"github.com/mine/lib"\n)\n',
             ".aigate/lite.toml": '[deps]\nprivate = ["github.com/corp/*"]\nfirst_party = ["github.com/mine"]\n'}
    assert not find(change(repo, files), "deps.hallucinated")


def test_nested_modules_use_their_own_go_mod(repo):
    project(repo, extra={"tools/go.mod": "module example.com/tools\n\ngo 1.21\n\nrequire github.com/gin-gonic/gin v1.9.1\n"})
    res = change(repo, {"tools/main.go": 'package main\n\nimport "github.com/gin-gonic/gin"\n',
                        "other.go": 'package main\n\nimport "github.com/gin-gonic/gin"\n'})
    assert [f["path"] for f in find(res, "deps.undeclared")] == ["other.go"]


# ---------------------------------------------------------------- the registry itself (no network)

class FakeProxy:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def __call__(self, url, method="GET", body=None):
        self.calls.append((method, url, body))
        got = self.routes.get(url)
        if isinstance(got, Exception):
            raise got
        return got if got is not None else (404, b"")


def registry(monkeypatch, routes):
    proxy = FakeProxy(routes)
    monkeypatch.setattr(aigate.Registry, "_http", lambda reg, url, method="GET", body=None: proxy(url, method, body))
    return aigate.Registry(use_cache=False), proxy


P = aigate.GO_PROXY


def test_go_escape_encodes_uppercase_and_keeps_slashes():
    assert aigate.go_escape("github.com/BurntSushi/toml") == "github.com/!burnt!sushi/toml"
    assert aigate.go_escape("github.com/a/b/v2") == "github.com/a/b/v2"


def test_info_reads_the_unsorted_version_list_and_the_first_versions_time(monkeypatch):
    reg, proxy = registry(monkeypatch, {
        f"{P}/github.com/a/b/@v/list": (200, b"v1.2.0\nv1.0.0\nv1.1.0-rc1\nv1.1.0\n"),
        f"{P}/github.com/a/b/@v/v1.0.0.info": (200, b'{"Version":"v1.0.0","Time":"2020-01-14T19:47:44Z"}')})
    info = reg.info("go", "github.com/a/b")
    assert info["created"] == "2020-01-14T19:47:44Z" and info["latest"] == "v1.2.0"
    assert list(info["versions"]) == ["v1.0.0", "v1.1.0-rc1", "v1.1.0", "v1.2.0"]


def test_info_for_missing_and_pseudo_only_modules(monkeypatch):
    reg, _p = registry(monkeypatch, {f"{P}/x.io/pseudo/@v/list": (200, b""),
                                     f"{P}/x.io/pseudo/@latest": (200, b'{"Version":"v0.0.0-20200101000000-abcdef123456","Time":"2020-01-01T00:00:00Z"}')})
    assert reg.info("go", "x.io/nope") == aigate.MISSING
    info = reg.info("go", "x.io/pseudo")
    assert info["versions"] == {} and info["created"] == "2020-01-01T00:00:00Z"


def test_exists_and_version_time(monkeypatch):
    reg, _p = registry(monkeypatch, {
        f"{P}/github.com/!burnt!sushi/toml/@latest": (200, b"{}"),
        f"{P}/github.com/a/b/@v/v1.0.0.info": (200, b'{"Version":"v1.0.0","Time":"2020-01-14T19:47:44Z"}'),
        f"{P}/github.com/a/boom/@latest": urllib.error.HTTPError("u", 500, "boom", {}, None)})
    assert reg.exists("go", "github.com/BurntSushi/toml") is True
    assert reg.exists("go", "github.com/a/missing") is False
    assert reg.exists("go", "github.com/a/boom") is None and reg.failures == 1
    assert reg.version_time("go", "github.com/a/b", "v1.0.0") == "2020-01-14T19:47:44Z"
    assert reg.version_time("go", "github.com/a/b", "v9.9.9") == aigate.MISSING


def test_osv_batch_is_one_call_for_many_packages(monkeypatch):
    answer = {"results": [{"vulns": [{"id": "GO-1", "modified": "x"}, {"id": "GHSA-2"}]}, {}, {"vulns": [{"id": "PYSEC-3"}]}]}
    reg, proxy = registry(monkeypatch, {"https://api.osv.dev/v1/querybatch": (200, json.dumps(answer).encode())})
    keys = [("osv", "go", "github.com/a/b", "v1.0.0"), ("osv", "go", "github.com/c/d", "v2.0.0"),
            ("osv", "pypi", "requests", "2.19.0")]
    reg.prefetch(keys)
    assert len(proxy.calls) == 1 and proxy.calls[0][0] == "POST"
    sent = json.loads(proxy.calls[0][2])["queries"]
    assert sent[0] == {"package": {"name": "github.com/a/b", "ecosystem": "Go"}, "version": "v1.0.0"}
    assert sent[2]["package"]["ecosystem"] == "PyPI"
    assert reg.vulns("go", "github.com/a/b", "v1.0.0") == ["GHSA-2", "GO-1"]
    assert reg.vulns("go", "github.com/c/d", "v2.0.0") == [] and reg.vulns("pypi", "requests", "2.19.0") == ["PYSEC-3"]
    assert len(proxy.calls) == 1 and reg.dirty


def test_osv_batch_failure_is_one_failure_and_unknown_answers(monkeypatch):
    reg, proxy = registry(monkeypatch, {"https://api.osv.dev/v1/querybatch": urllib.error.URLError("down")})
    reg.prefetch([("osv", "go", "a/b", "v1.0.0"), ("osv", "go", "c/d", "v1.0.0")])
    assert reg.failures == 1 and len(proxy.calls) == 1
    assert reg.vulns("go", "a/b", "v1.0.0") is None and len(proxy.calls) == 1


def test_a_single_osv_lookup_still_uses_the_query_endpoint(monkeypatch):
    reg, proxy = registry(monkeypatch, {"https://api.osv.dev/v1/query": (200, b'{"vulns":[{"id":"GO-9"}]}')})
    assert reg.vulns("go", "github.com/a/b", "v1.0.0") == ["GO-9"]
    assert json.loads(proxy.calls[0][2])["package"]["ecosystem"] == "Go"


def test_candidates_for_import_paths():
    c = aigate.go_candidates
    assert c("github.com/gin-gonic/gin/binding") == ["github.com/gin-gonic/gin"]
    assert c("github.com/go-chi/chi/v5/middleware") == ["github.com/go-chi/chi/v5", "github.com/go-chi/chi"]
    assert c("gopkg.in/yaml.v3") == ["gopkg.in/yaml.v3"]
    # was ["go.uber.org/zap", "go.uber.org/zap/zapcore"]: unknown hosts now try every plausible root, longest first
    # (the longest existing one is the module, a nested module wins over its parent)
    assert c("go.uber.org/zap/zapcore") == ["go.uber.org/zap/zapcore", "go.uber.org/zap"]
    assert c("golang.org/x/net/http2") == ["golang.org/x/net"]


def test_go_private_pattern_semantics(monkeypatch):
    monkeypatch.setenv("GOPRIVATE", "*.corp.io,github.com/acme/*")
    assert aigate.go_private("git.corp.io/team/x") and aigate.go_private("github.com/acme/tool/v2")
    assert not aigate.go_private("github.com/other/tool") and not aigate.go_private("corp.io/x")


def test_go_env_file_location_follows_the_platform_config_dir(tmp_path, monkeypatch):
    mac = tmp_path / "Library" / "Application Support" / "go"
    mac.mkdir(parents=True)
    (mac / "env").write_text("GOPRIVATE=corp.example/*\n")
    for var in ("GOENV", "GOPRIVATE", "XDG_CONFIG_HOME"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setattr(aigate.os, "name", "posix")
    monkeypatch.setattr(aigate.sys, "platform", "darwin")
    monkeypatch.setattr(aigate, "_GO_ENV_FILE", {})
    assert aigate.go_setting("GOPRIVATE") == "corp.example/*"
