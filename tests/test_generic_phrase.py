"""Round 3: secrets.generic also catches a secret-word NAME assigned a multi-word passphrase in non-test source.

    db_password = "correct horse battery staple"

Stricter than before, so a person and an AI agent get the same result (fix). The risk is a new false positive, so the value
must look like a passphrase and not like a message: no punctuation, no word of a message or a UI prompt, no secret word
inside the value, and the file is not a test, documentation or a translation file.

Decision on a sentence-like message without punctuation (`bad_token = "invalid token supplied by caller"`): silent.
Blocking a commit on a log or error message is the costlier mistake in real code, and a message nearly always carries a
function word (the, is, not, must, invalid, ...) or the secret word itself; a random passphrase almost never does. The cost
is a missed passphrase made of such words, which the entropy and provider rules do not catch either.

Also silent (second commit): text that says the value lives elsewhere ("stored in vault", "injected at deploy time"), a
comparison (`==`), and test-support directories and files (testdata, fixtures, testutil, mocks ...) for a person and an agent alike. examples/, samples/,
scripts/ and seeds/ are NOT excluded.

A real passphrase is a leak wherever it sits, so a keyword argument, a parameter default and a name such as DEFAULT_PASSWORD,
EMPTY_TOKEN or default_secret all fire. The smaller rule: no name-based sentinel list. Absence is read from the VALUE
stop-words only (`NO_TOKEN = "no value given"` is silent, `EMPTY_TOKEN = "winter garden lantern"` fires).

Known limit: UI text in another language ("Mot de passe actuel" assigned to `password` outside a translation file) cannot
be told from a passphrase by a word list and still fires, equally for a person and an agent."""
import pytest

from conftest import find

AGENT = {"CLAUDECODE": "1"}


def actions(res, path):
    return sorted(f["action"] for f in find(res, "secrets.generic")
                  if f["path"] == path and (not f["allowed"] or f["allowed"] == "info only"))


def both(repo, path, text):
    repo.write(path, text)
    repo.add()
    return actions(repo.check("--offline"), path), actions(repo.check("--offline", env=AGENT), path)


POSITIVE = {
    "py-snake": ("app/config.py", 'db_password = "correct horse battery staple"\n'),
    "py-fstring-prefix": ("app/config.py", 'db_password = r"correct horse battery staple"\n'),
    "py-dict-key": ("app/config.py", 'CONFIG = {"password": "correct horse battery staple"}\n'),
    "ts-const": ("src/config.ts", "const password = 'blue falcon sings at noon'\n"),
    "ts-camel": ("src/config.ts", 'export const apiSecret = "purple monkey dishwasher pass";\n'),
    "go-field": ("shop/auth.go", 'var c = Config{\n\tSecret: "orange moon over water",\n}\n'),
    "go-const": ("shop/auth.go", 'package shop\n\nconst authToken = "tiger lily ocean breeze"\n'),
    "go-short-decl": ("shop/auth.go", 'func f() {\n\tpassword := "correct horse battery staple"\n}\n'),
    "capitalised": ("app/config.py", 'API_KEY = "Correct Horse Battery Staple"\n'),
    "hyphen-word": ("app/config.py", 'passphrase = "correct-horse battery staple"\n'),
    "yaml": ("config/app.yml", 'password: "correct horse battery staple"\n'),
    "java": ("src/Config.java", 'String dbPassword = "correct horse battery staple";\n'),
    # a real passphrase is a leak wherever it sits: a keyword argument, a parameter default, a default_/empty_ name
    "kwarg-call": ("app/m.py", 'connect(host, password="correct horse battery staple")\n'),
    "kwarg-nested": ("app/m.py", 'foo(bar(1), password="blue falcon sings at noon")\n'),
    "param-default": ("app/m.py", 'def connect(host, password="winter garden lantern"):\n    pass\n'),
    "name-default-password": ("app/m.py", 'DEFAULT_PASSWORD = "winter garden lantern"\n'),
    "name-default-secret": ("app/m.py", 'default_secret = "correct horse battery staple"\n'),
    "name-empty-token": ("app/m.py", 'EMPTY_TOKEN = "winter garden lantern"\n'),
    "examples-dir":("examples/app.py", 'db_password = "correct horse battery staple"\n'),
    "samples-dir": ("samples/app.py", 'db_password = "correct horse battery staple"\n'),
    "scripts-dir": ("scripts/app.py", 'db_password = "correct horse battery staple"\n'),
    "seeds-dir": ("db/seeds/app.py", 'db_password = "correct horse battery staple"\n'),
    "after-a-closed-call": ("app/config.py", 'x = f(1); password = "winter garden lantern"\n'),
    "known-limit-other-language": ("app/m.py", 'password = "Mot de passe actuel"\n'),
    "secret-key":("app/config.py", 'SECRET_KEY = "winter garden lantern"\n'),
    "exactly-12":("app/config.py", 'token = "abc def ghij"\n'),
}


@pytest.mark.parametrize("name", sorted(POSITIVE))
def test_a_secret_word_assigned_a_passphrase_is_a_fix_for_a_person_and_an_agent(repo, name):
    path, text = POSITIVE[name]
    person, agent = both(repo, path, text)
    assert person == ["fix"], person
    assert agent == ["fix"], agent


def test_the_agent_result_is_never_softer_than_the_persons(repo):
    person, agent = both(repo, "app/config.py", 'db_password = "correct horse battery staple"\n')
    assert person == agent == ["fix"]


def test_an_e2e_file_keeps_the_round_2_person_only_softening(repo):
    person, agent = both(repo, "e2e/config.ts", "const password = 'blue falcon sings at noon'\n")
    assert person == ["warn"] and agent == ["fix"]       # the agent is not softened


# ---------------------------------------------------------------- negatives: nothing new fires

NEGATIVE = {
    # sentences with end punctuation (the corpus case r3-py-generic-sentence-punct)
    "sentence-period": ("app/m.py", 'secret_hint = "The secret is stored in the vault."\n'),
    "sentence-bang": ("app/m.py", 'token_notice = "Your token has been rotated!"\n'),
    "sentence-question": ("app/m.py", 'password_prompt = "Is the password correct?"\n'),
    "sentence-colon": ("app/m.py", 'bad_password = "correct horse battery:"\n'),
    # too short (r3-ts-generic-short-value) or no space (existing rules decide)
    "short": ("src/auth.ts", 'export const password = "blue horse";\n'),
    "eleven": ("src/auth.ts", 'const token = "abc def ghi";\n'),
    # UI labels
    "ui-label": ("app/forms.py", 'password_label = "Enter your password here"\n'),
    "ui-enter": ("app/forms.py", 'the_password = "Enter the account value"\n'),
    "ui-please": ("app/forms.py", 'pwd = "Please wait while loading"\n'),
    "ui-your": ("app/forms.py", 'password = "Your account details"\n'),
    "ui-type": ("app/forms.py", 'secret = "Type something memorable"\n'),
    "ui-choose": ("app/forms.py", 'password = "Choose something memorable"\n'),
    "ui-confirm": ("app/forms.py", 'password = "Confirm something memorable"\n'),
    "ui-select": ("app/forms.py", 'token = "Select something memorable"\n'),
    "ui-forgot": ("app/forms.py", 'password = "Forgot something memorable"\n'),
    "ui-reset": ("app/forms.py", 'token = "Reset something memorable"\n'),
    # messages without end punctuation, assigned to a secret-word name that NAME_EXCLUDE does not cover
    "msg-invalid-token": ("app/m.py", 'bad_token = "invalid token supplied by caller"\n'),
    "msg-excluded-name": ("app/m.py", 'token_error = "invalid token supplied by caller"\n'),
    "msg-password-prompt": ("app/m.py", 'password_prompt = "Enter the password for the account"\n'),
    "msg-expired": ("app/m.py", 'expired_token = "session has expired please log in"\n'),
    "msg-not-match": ("app/m.py", 'password_mismatch = "passwords do not match"\n'),
    "msg-missing": ("app/m.py", 'missing_secret = "required value missing from request"\n'),
    "msg-denied": ("app/m.py", 'token_denied = "access denied for this user"\n'),
    "msg-must": ("app/m.py", 'token_rule = "must contain letters and digits"\n'),
    "msg-cannot": ("app/m.py", 'secret_failure = "cannot read the secret store"\n'),
    # the secret word, or another credential word, inside the value: it talks about the secret
    "secret-santa": ("app/m.py", 'secret_name = "my secret santa list"\n'),
    "secret-santa-2": ("app/m.py", 'secret_group = "office secret santa list"\n'),
    "word-in-value": ("app/m.py", 'secret_title = "reset password link sent"\n'),
    "api-key-value": ("app/m.py", 'secret_text = "paste your api key above"\n'),
    # templates and formats
    "percent-s": ("app/m.py", 'token_text = "bad token %s given"\n'),
    "braces": ("app/m.py", 'token_text = "bad value {} given"\n'),
    "braces-0": ("app/m.py", 'token_text = "bad value {0} given"\n'),
    "dollar-brace": ("src/m.ts", "const token_text = `bad value ${name} given`;\n"),
    "named-format": ("app/m.py", 'token_text = "bad value {name} given"\n'),
    # placeholders
    "your-underscore": ("app/m.py", 'password = "your_password here ok"\n'),
    "changeme": ("app/m.py", 'password = "changeme later today ok"\n'),
    "example": ("app/m.py", 'password = "example value for docs"\n'),
    "xxx": ("app/m.py", 'password = "xxx xxx xxx xxx"\n'),
    # other shapes of text: apostrophe, commas, symbols, SQL
    "apostrophe": ("app/m.py", 'password = "don\'t share this value"\n'),
    "comma": ("app/m.py", 'bad_password = "alpha beta, gamma delta"\n'),
    "sql": ("app/m.py", 'secret_query = "SELECT * FROM tokens WHERE id"\n'),
    "slash": ("app/m.py", 'secret_dir = "some folder/with spaces"\n'),
    # names that are not a secret word
    "name-tokenizer": ("app/m.py", 'tokenizer_text = "alpha beta gamma delta"\n'),
    "name-passwordless": ("app/m.py", 'passwordless_mode = "alpha beta gamma delta"\n'),
    "name-session": ("app/m.py", 'session_name = "alpha beta gamma delta"\n'),
    # the secret word must end the name: a lexer's token types (found in the pinned jinja clone) are not secrets
    "name-token-first": ("src/lexer.py", 'TOKEN_COMMENT_BEGIN = "begin of comment"\nTOKEN_EOF = "end of template"\n'),
    "name-token-middle": ("src/lexer.py", 'token_kind_label = "begin of comment"\ntoken_type_x = "end of template"\n'),
    "name-secret-first": ("app/m.py", 'secret_store_path = "alpha beta gamma delta"\nsecret_santa = "alpha beta gamma delta"\n'),
    "name-notoken":("app/m.py", 'description = "alpha beta gamma delta"\n'),
    # variable references and calls are not literals
    "call": ("app/m.py", 'password = get("correct horse battery staple")\n'),
    "no-quote": ("app/m.py", "password = correct horse battery staple\n"),
    # tests (r3-go-generic-test-file) and documentation keep today's behaviour
    "go-test": ("shop/auth_test.go", 'func TestLogin(t *testing.T) {\n\tpassword := "correct horse battery staple"\n}\n'),
    "py-test": ("tests/test_auth.py", 'password = "correct horse battery staple"\n'),
    "ts-spec": ("src/auth.spec.ts", "const password = 'correct horse battery staple';\n"),
    "markdown": ("docs/guide.md", 'db_password = "correct horse battery staple"\n'),
    "readme": ("README.md", 'password = "correct horse battery staple"\n'),
    "rst": ("docs/setup.rst", 'password = "correct horse battery staple"\n'),
    # pointers and sentinels: the text says the value lives elsewhere
    "ptr-vault": ("app/m.py", 'token = "stored in vault"\n'),
    "ptr-keychain": ("app/m.py", 'password = "stored in keychain"\n'),
    "ptr-see-vault": ("app/m.py", 'password = "see vault for value"\n'),
    "ptr-to-be-defined": ("app/m.py", 'password = "to be defined"\n'),
    "ptr-to-be-set-later": ("app/m.py", 'secret = "to be set later"\n'),
    "ptr-injected": ("app/m.py", 'token = "injected at deploy time"\n'),
    "ptr-read-file": ("app/m.py", 'token = "read from file"\n'),
    "ptr-read-stdin": ("app/m.py", 'token = "read from stdin"\n'),
    "ptr-env-var": ("app/m.py", 'token = "from env var"\n'),
    "ptr-1password": ("app/m.py", 'password = "see 1password entry"\n'),
    "ptr-via-env": ("app/m.py", 'password = "set via environment variable"\n'),
    "ptr-sh": ("deploy/run.sh", 'export TOKEN="injected at deploy time"\n'),
    "ptr-yaml": ("config/app.yml", 'password: "injected at deploy time"\n'),
    "sentinel-no": ("app/m.py", 'NO_TOKEN = "no value given"\n'),
    "sentinel-empty-words": ("app/m.py", 'EMPTY_TOKEN = "empty value given"\n'),
    "sentinel-invalid-words": ("app/m.py", 'INVALID_TOKEN = "none value set"\n'),
    "shared-store-yaml": ("config/app.yml", 'secret: "projects shared store"\n'),
    "shared-store-tf": ("infra/main.tf", 'secret = "projects shared store"\n'),
    "shared-store-toml": ("config/app.toml", 'secret = "projects shared store"\n'),
    "shared-store-json": ("config/app.json", '{"secret": "projects shared store"}\n'),
    "ui-toggle": ("config/app.yml", 'password: "show hide toggle"\n'),
    "ui-link": ("src/m.ts", "const x = { token: 'single use link' };\n"),
    "my-api-service": ("app/m.py", 'api_key = "my api service"\n'),
    "my-team-value": ("app/m.py", 'password = "my team value here"\n'),
    # a comparison, a keyword argument and a parameter default are not an assignment of a secret
    "cmp-free-tier": ("app/m.py", 'if password == "free tier access":\n    pass\n'),
    "cmp-passphrase": ("app/m.py", 'if password == "winter garden lantern":\n    pass\n'),
    "cmp-assert": ("app/m.py", 'assert token == "no value given"\n'),
    "cmp-assert-passphrase": ("app/m.py", 'assert token == "winter garden lantern"\n'),
    "cmp-ne": ("app/m.py", 'if password != "winter garden lantern":\n    pass\n'),
    "dict-no-value": ("app/m.py", 'def f():\n    return {"token": "no value given"}\n'),
    "kwarg-no-value": ("app/m.py", 'foo(password="no value given")\n'),
    # test-support directories and files are test code for this rule, for a person and an agent
    "sup-testdata": ("shop/testdata/auth.go", 'package shop\n\nconst authToken = "tiger lily ocean breeze"\n'),
    "sup-fixtures-dir": ("shop/fixtures/users.py", 'db_password = "correct horse battery staple"\n'),
    "sup-testutil": ("internal/testutil/auth.go", 'package testutil\n\nconst authToken = "tiger lily ocean breeze"\n'),
    "sup-testutils": ("internal/testutils/auth.go", 'package testutils\n\nconst authToken = "tiger lily ocean breeze"\n'),
    "sup-test-utils": ("app/test_utils/auth.py", 'db_password = "correct horse battery staple"\n'),
    "sup-testing": ("pkg/testing/auth.go", 'package testing\n\nconst authToken = "tiger lily ocean breeze"\n'),
    "sup-tests-helpers": ("app/tests_helpers/auth.py", 'db_password = "correct horse battery staple"\n'),
    "sup-suffix-fixtures": ("app/user_fixtures.py", 'db_password = "correct horse battery staple"\n'),
    "sup-fixtures-py": ("app/fixtures.py", 'db_password = "correct horse battery staple"\n'),
    "sup-mock-py": ("app/mock_auth.py", 'db_password = "correct horse battery staple"\n'),
    "sup-mocks-dir": ("src/mocks/auth.ts", "const password = 'blue falcon sings at noon';\n"),
    # translation files
    "i18n-json": ("src/locales/fr.json", '{"password": "Mot de passe actuel"}\n'),
    "i18n-dir": ("src/i18n/en.ts", 'export default { token: "Jeton de connexion actuel" }\n'),
    "po-file": ("po/fr.po", 'password = "Mot de passe actuel"\n'),
}


@pytest.mark.parametrize("name", sorted(NEGATIVE))
def test_nothing_new_fires(repo, name):
    path, text = NEGATIVE[name]
    person, agent = both(repo, path, text)
    assert person == [] and agent == [], (person, agent)


def test_a_message_that_carries_no_function_word_is_the_known_blind_spot(repo):
    # documented decision: only function words and the secret word separate a message from a passphrase; a bare
    # three-word noun phrase is read as a passphrase (and fires, equally for a person and an agent)
    person, agent = both(repo, "app/m.py", 'db_password = "winter garden lantern"\n')
    assert person == agent == ["fix"]


def test_the_existing_single_token_rule_is_unchanged(repo):
    person, agent = both(repo, "app/m.py", 'password = "Zx9kQ2vB7mN4pL1w"\n')
    assert person == agent == ["fix"]
