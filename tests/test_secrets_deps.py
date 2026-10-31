from conftest import find, rules

AWS = "AKIA" + "QWERTYUIOPASDFGH"            # split so this file is not itself a finding


# ---------------------------------------------------------------- secrets

def test_provider_key_blocks(repo):
    repo.write("app/config.py", f'KEY = "{AWS}"\n')
    repo.add()
    res = repo.check()
    assert res["verdict"] == "block" and res["code"] == 1
    f = find(res, "secrets.hardcoded")[0]
    assert f["line"] == 1 and AWS not in f["message"]          # the secret itself is never printed


def test_documentation_placeholder_key_is_ignored(repo):
    repo.write("docs.py", 'KEY = "AKIAIOSFODNN7EXAMPLE"\n')
    repo.add()
    assert "secrets.hardcoded" not in rules(repo.check())


def test_generic_secret_fix_but_env_var_name_and_placeholders_pass(repo):
    repo.write("svc.py", 'password = "Sup3rS3cretValue!"\nAPI_KEY_ENV = "OPENAI_API_KEY"\n'
                         'token = "your-token-here"\napi_key = os.environ["API_KEY"]\n')
    repo.add()
    found = find(repo.check(), "secrets.generic")
    assert [f["line"] for f in found] == [1] and found[0]["action"] == "fix"


def test_generic_secret_in_tests_is_only_a_warning(repo):
    repo.write("tests/test_api.py", 'def test_x():\n    secret = "Zx9!kQ2#vB7$mN4p"\n    assert secret\n')
    repo.add()
    assert find(repo.check(), "secrets.generic")[0]["action"] == "warn"


def test_env_files(repo):
    repo.write(".env", "DATABASE_URL=postgres://app:9fK2xQ7pLm@db.prod.acme.net/app\nDEBUG=true\n")
    repo.write(".env.example", "DATABASE_URL=\nAPI_KEY=your-key\n")
    repo.write("web/.env.production", "NEXT_PUBLIC_SITE=https://x.dev\n")
    repo.add()
    res = repo.check()
    env = {f["path"]: f["action"] for f in find(res, "secrets.env_file")}
    assert env == {".env": "block", "web/.env.production": "warn"}


def test_private_key_file_blocks_but_certificate_passes(repo):
    repo.write("certs/server.pem", "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n")
    repo.write("keys/deploy.key", "-----BEGIN OPENSSH PRIVATE KEY-----\nb3Bl\n-----END OPENSSH PRIVATE KEY-----\n")
    repo.add()
    res = repo.check()
    assert [f["path"] for f in find(res, "secrets.env_file")] == ["keys/deploy.key"]
    assert find(res, "secrets.hardcoded")                       # the key header itself


def test_local_connection_string_is_warning(repo):
    repo.write("settings.py", 'DB = "postgres://app:h7Gk2pQw9z@localhost:5432/app"\n')
    repo.add()
    assert find(repo.check(), "secrets.generic")[0]["action"] == "warn"


def test_untracked_env_not_ignored_warns(repo):
    (repo.path / ".env").write_text("X=1\n")
    repo.write("a.py", "x = 1\n")
    repo.add("a.py")
    res = repo.check()
    assert find(res, "secrets.env_not_ignored")[0]["action"] == "warn"


# ---------------------------------------------------------------- dependencies

def test_requirements_hallucinated_new_and_untrusted(repo):
    repo.write("requirements.txt", "ghost-pkg==1.0\nbrand-new-pkg\n--extra-index-url https://user:tok@evil.example/simple\n"
                                   "mylib @ git+https://github.com/x/mylib.git\nflask>=3\n")
    repo.add()
    res = repo.check()
    assert find(res, "deps.hallucinated")[0]["line"] == 1
    assert find(res, "deps.new_package")[0]["line"] == 2
    untrusted = find(res, "deps.untrusted_source")
    assert len(untrusted) == 2 and all("tok" not in f["message"] for f in untrusted)
    assert res["verdict"] == "block"


def test_pinned_version_checks(repo):
    repo.write("requirements.txt", "requests==2.19.0\npydantic==1.10.2\n")
    repo.add()
    res = repo.check()
    assert "GHSA-x84v-xcm2-53pg" in find(res, "deps.vulnerable")[0]["message"]
    assert "2.12.0" in find(res, "deps.outdated")[0]["message"]


def test_cooldown_for_fresh_pin_and_fresh_range(repo):
    repo.write("requirements.txt", "requests==2.33.0\n")
    repo.add()
    assert find(repo.check(), "deps.version_cooldown")[0]["action"] == "fix"
    repo.write("requirements.txt", "requests>=2.30\n")
    repo.add()
    assert find(repo.check(), "deps.version_cooldown")[0]["action"] == "warn"


def test_security_bump_is_exempt_from_cooldown(repo):
    repo.write("requirements.txt", "requests==2.19.0\n")
    repo.commit()
    repo.write("requirements.txt", "requests==2.33.0\n")
    repo.add()
    assert not find(repo.check(), "deps.version_cooldown")


def test_hallucinated_version(repo):
    repo.write("requirements.txt", "flask==9.9.9\n")
    repo.add()
    assert "no version 9.9.9" in find(repo.check(), "deps.hallucinated")[0]["message"]


def test_typosquat(repo):
    repo.write("requirements.txt", "reqeusts\n")
    repo.add()
    assert "'requests'" in find(repo.check(), "deps.typosquat")[0]["message"]


def test_trusted_source_and_private_package(repo):
    repo.write(".aigate/lite.toml", '[deps]\ntrusted_sources = ["https://pypi.acme.dev/"]\nprivate = ["acme-*"]\n')
    repo.commit()
    repo.write("requirements.txt", "--extra-index-url https://pypi.acme.dev/simple\nacme-utils==1.0\n")
    repo.add()
    res = repo.check()
    assert not find(res, "deps.untrusted_source") and not find(res, "deps.hallucinated")


def test_python_imports(repo):
    repo.write("requirements.txt", "flask\n")
    repo.write("mypkg/__init__.py", "")
    repo.commit()
    repo.write("mypkg/core.py", "import json\nimport mypkg.helpers\nimport flask\nimport yaml\nimport httpx\n"
                                "import fastapi_auth_helperz\ntry:\n    import ghost_optional\nexcept ImportError:\n"
                                "    ghost_optional = None\nimport distutils\n")
    repo.add()
    res = repo.check()
    halluc = {f["line"]: f["action"] for f in find(res, "deps.hallucinated")}
    assert halluc == {6: "block", 8: "warn"}
    undeclared = [f["line"] for f in find(res, "deps.undeclared")]
    assert 4 in undeclared and 5 in undeclared and 3 not in undeclared
    assert find(res, "deps.removed_stdlib")[0]["line"] == 11


def test_import_map_and_moved_imports_are_not_rechecked(repo):
    repo.write("requirements.txt", "pyyaml\n")
    repo.write("a.py", "import ghost_pkg\n")
    repo.commit()
    repo.write("a.py", "import yaml\n\nimport ghost_pkg\n")          # ghost_pkg was already there
    repo.add()
    res = repo.check()
    assert not find(res, "deps.hallucinated") and not find(res, "deps.undeclared")


def test_unreachable_registry_is_a_warning_and_can_block(repo):
    repo.write("requirements.txt", "unknown-pkg\n")
    repo.add()
    assert find(repo.check(), "deps.unverified")[0]["action"] == "warn"
    repo.write(".aigate/lite.toml", '[gate]\noffline = "block"\n')
    repo.add()
    assert find(repo.check(), "deps.unverified")[0]["action"] == "block"


def test_package_json_and_js_imports(repo):
    repo.write("package.json", '{"name": "web", "dependencies": {"react": "^19.0.0"}}\n')
    repo.write("tsconfig.json", '{\n  // comment\n  "compilerOptions": {"paths": {"@app/*": ["src/*"]},},\n}\n')
    repo.commit()
    repo.write("package.json", '{"name": "web", "dependencies": {"react": "^19.0.0", "ghost-js": "^1.0.0",'
                               ' "left-pad": "1.3.0", "evil": "github:someone/evil"}}\n')
    repo.write("src/App.tsx", "import React from 'react'\nimport fs from 'node:fs'\nimport path from 'path'\n"
                              "import x from '@app/util'\nimport y from './local'\nimport z from 'ghost-ui-kit'\n"
                              "import _ from 'lodash'\n")
    repo.add()
    res = repo.check()
    halluc = sorted((f["path"], f["line"]) for f in find(res, "deps.hallucinated"))
    assert halluc == [("package.json", 1), ("src/App.tsx", 6)]
    assert [f["line"] for f in find(res, "deps.undeclared")] == [7]
    assert find(res, "deps.untrusted_source") and find(res, "deps.deprecated")


def test_lockfile_untrusted_source(repo):
    repo.write("package-lock.json", '{\n  "packages": {\n    "node_modules/a": {\n'
                                    '      "resolved": "https://registry.npmjs.org/a/-/a-1.0.0.tgz"\n    },\n'
                                    '    "node_modules/b": {\n      "resolved": "git+ssh://git@github.com/x/b.git#abc"\n    }\n  }\n}\n')
    repo.add()
    found = find(repo.check(), "deps.untrusted_source")
    assert len(found) == 1 and found[0]["line"] == 7
