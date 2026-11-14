"""Round 2: false positives that only a person is spared (information only, "pass"); an AI agent's result is unchanged.
Each softening has a person test, an agent-unchanged test and negatives: a real secret in the same shape still warns."""
import pytest

from conftest import find

AGENT = {"CLAUDECODE": "1"}


def shown(res, rule, path):
    """The actions of `rule` on `path`; "pass" is a finding that is information only."""
    return sorted("pass" if f["allowed"] == "info only" else f["action"] for f in find(res, rule)
                  if f["path"] == path and (not f["allowed"] or f["allowed"] == "info only"))


def person_and_agent(repo, path, text, rule="secrets.generic"):
    repo.write(path, text)
    repo.add()
    return shown(repo.check("--offline"), rule, path), shown(repo.check("--offline", env=AGENT), rule, path)


CI = ".github/workflows/ci.yml"
CI_DSN = 'jobs:\n  t:\n    env:\n      MIGRATE_URL: "postgres://atlas:atlas@localhost:5432/atlas?sslmode=disable"\n'
# (path, text, agent's action today): a person gets "pass"; the agent's result must stay as it is
SOFTENED = {
    "ci-localhost-dsn": (CI, CI_DSN, "warn"),
    "ci-localhost-dsn-2": (".github/workflows/ci-stability.yml", CI_DSN, "warn"),
    "sprintf-dsn-in-test": ("internal/auth/resolver_test.go",
                            'func TestR(t *testing.T) {\n\tdb, _ := sql.Open("pgx", '
                            'fmt.Sprintf("postgres://atlas:atlas@%s/atlas?sslmode=disable", addr))\n}\n', "warn"),
    "docs-export-password": ("infra/vault/README.md",
                             "# Vault\n\n```\nexport ATLAS_DEV_RDS_PASSWORD='a-strong-dev-password'\n```\n", "fix"),
    "literal-plus-call": ("internal/orgs/membership_test.go",
                          'func TestX(t *testing.T) {\n\ttoken := "uac-token-" + uniqueRun("t")\n}\n', "warn"),
    "secret-id-path": ("internal/secrets/provider_test.go",
                       'func TestX(t *testing.T) {\n\tp, err := New(c, Options{SecretID: "atlas/jwt-signing-keys", TTL: ttl})\n}\n',
                       "warn"),
    "words-in-json": ("internal/auth/handler_test.go",
                      'func TestX(t *testing.T) {\n\tu := post(t, mux, P, `{"token":"definitely-not-a-token"}`)\n}\n', "warn"),
    "challenge-snake": ("src/lib/api/errors.test.ts",
                        "test('x', () => {\n  const v = problem('m', { challenge_token: 'challenge-1', expires_in: 240 });\n});\n",
                        "warn"),
    "challenge-camel": ("src/lib/api/errors.test.ts",
                        "test('x', () => {\n  const v = {\n    challengeToken: 'challenge-2',\n  };\n});\n", "warn"),
}


@pytest.mark.parametrize("name", sorted(SOFTENED))
def test_a_person_is_only_informed(repo, name):
    path, text, _ = SOFTENED[name]
    person, _agent = person_and_agent(repo, path, text)
    assert person == ["pass"]


@pytest.mark.parametrize("name", sorted(SOFTENED))
def test_the_agent_result_is_unchanged(repo, name):
    path, text, agent_action = SOFTENED[name]
    _person, agent = person_and_agent(repo, path, text)
    assert agent == [agent_action]


def test_mock_random_secret_in_e2e_is_information_for_a_person_only(repo):
    path = "e2e/mock-api.mjs"
    text = ("const BASE32 = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567';\n"
            "const secret = Array.from({ length: 32 }, () => BASE32[Math.floor(Math.random() * BASE32.length)]).join('');\n")
    person, agent = person_and_agent(repo, path, text, "security.weak_crypto")
    assert person == ["pass"]


def test_mock_random_secret_agent_result_is_unchanged(repo):
    path = "e2e/mock-api.mjs"
    text = ("const BASE32 = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567';\n"
            "const secret = Array.from({ length: 32 }, () => BASE32[Math.floor(Math.random() * BASE32.length)]).join('');\n")
    _person, agent = person_and_agent(repo, path, text, "security.weak_crypto")
    assert agent == ["warn"]


@pytest.mark.parametrize("path", ["src/auth/__mocks__/session.js", "src/auth/mocks/session.js", "e2e/auth/token.js"])
def test_mock_random_secret_under_a_sensitive_path_is_not_softened_for_a_person(repo, path):
    text = "const secret = Array.from({ length: 32 }, () => B[Math.floor(Math.random() * B.length)]).join('');\n"
    person, agent = person_and_agent(repo, path, text, "security.weak_crypto")
    assert person == agent == ["fix"]            # the label only spares warn; a sensitive path keeps its floor


# ---------------------------------------------------------------- negatives: a real secret in the same shape still warns

REAL = "Zx9kQ2vB7mN4pL1w"
NEGATIVES = {
    # a real-looking password for a localhost database in a workflow
    "ci-real-password": (CI, f'jobs:\n  t:\n    env:\n      URL: "postgres://atlas:{REAL}@localhost:5432/atlas"\n'),
    # the same user:user DSN on a remote host
    "ci-remote-host": (CI, 'jobs:\n  t:\n    env:\n      URL: "postgres://atlas:atlas@db.internal-prod.net:5432/atlas"\n'),
    # a localhost user:user DSN in application code is still a warning
    "app-localhost": ("app/config.go", 'var dsn = "postgres://atlas:atlas@localhost:5432/atlas"\n'),
    "sprintf-real-password": ("internal/resolver_test.go",
                              f'func T() {{\n\tdsn := fmt.Sprintf("postgres://atlas:{REAL}@%s/atlas", addr)\n}}\n'),
    "sprintf-not-in-a-test": ("internal/db.go", 'func T() {\n\tdsn := fmt.Sprintf("postgres://atlas:atlas@%s/atlas", addr)\n}\n'),
    "docs-export-real-password": ("infra/vault/README.md", f"# Vault\n\n```\nexport X_PASSWORD='{REAL}'\n```\n"),
    "words-in-code": ("app/db.go", 'func T() {\n\tpassword := "a-strong-dev-password"\n}\n'),
    "words-assigned-in-test": ("internal/config_test.go", 'func T() {\n\tcfg.JWT.Secret = "a-sufficiently-long-secret"\n}\n'),
    "test-real-password": ("internal/x_test.go", f'func T() {{\n\tpassword := "{REAL}"\n}}\n'),
    "concat-real-prefix": ("internal/x_test.go", f'func T() {{\n\ttoken := "{REAL}-" + uniqueRun("t")\n}}\n'),
    "concat-literals": ("internal/x_test.go", 'func T() {\n\ttoken := "uac-token-" + "plainliteral"\n}\n'),
    "secret-id-high-entropy": ("internal/x_test.go", f'func T() {{\n\to := Options{{SecretID: "{REAL}/Qm8tY3uE"}}\n}}\n'),
    "json-real-token": ("internal/x_test.go", f'func T() {{\n\tu := post(t, `{{"token":"{REAL}"}}`)\n}}\n'),
    "challenge-real-token": ("src/lib/api/errors.test.ts", f"const v = {{ challenge_token: '{REAL}' }};\n"),
    "short-id-not-in-a-test": ("src/lib/auth.ts", "const v = { password: 'challenge-1' };\n"),
    # the path-shaped and concatenated shapes are softened in test files only, never in ordinary source
    "path-id-in-source": ("src/main.go", 'var o = Options{SecretID: "prod/db/master-password"}\n'),
    "path-value-in-python-source": ("src/a.py", 'password = "hunter/two/three"\n'),
    "concat-in-source": ("src/main.go", 'var token = "uac-token-" + unique("t")\n'),
}


@pytest.mark.parametrize("name", ["path-id-in-source", "path-value-in-python-source", "concat-in-source"])
def test_path_and_concat_shapes_in_source_stay_fix_for_a_person_and_an_agent(repo, name):
    path, text = NEGATIVES[name]
    person, agent = person_and_agent(repo, path, text)
    assert person == ["fix"] and agent == ["fix"]


@pytest.mark.parametrize("name", sorted(NEGATIVES))
def test_a_real_secret_in_the_same_shape_still_warns(repo, name):
    path, text = NEGATIVES[name]
    person, agent = person_and_agent(repo, path, text)
    assert person and "pass" not in person
    assert agent and "pass" not in agent


def test_weak_random_outside_e2e_and_a_weak_hash_in_e2e_still_warn(repo):
    random_line = "const secret = Array.from({ length: 8 }, () => Math.floor(Math.random() * 9)).join('');\n"
    hash_line = "const h = crypto.createHash('md5').update(password).digest('hex');\n"
    person, _ = person_and_agent(repo, "src/server.mjs", random_line, "security.weak_crypto")
    assert person == ["warn"]
    person, _ = person_and_agent(repo, "e2e/mock-hash.mjs", hash_line, "security.weak_crypto")
    assert person == ["warn"]
