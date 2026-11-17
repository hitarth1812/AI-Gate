#!/usr/bin/env python3
"""Corpus v3 (round 3): 37 labelled TRAINING cases written to eval/corpus/ (never to eval/holdout/).

    py eval/tools/seeds/seed_v3.py

They were written from what README.md and docs/RULES.md say each rule should do, NOT from what the tool prints today, and no
tool output was looked at. Some of them are meant to FAIL today (constant-folded provider keys in JS/TS/Go, the multi-word
`secrets.generic` behaviour, a few claim_mismatch and skip boundaries); the tool is fixed against them afterwards. A label is
never tuned to the tool's current output.

  A. secrets.hardcoded (18): 8 constant-fold shapes in JS/TS/Go, 6 keys in unusual files, 4 look-alikes that must NOT fire
     review.claim_mismatch (7): 5 claims the diff does not support, 2 true claims that must not fire
     tests.skip_added (5): boundaries of the rule
  B. secrets.generic (7): a secret-word name assigned a multi-word string with spaces: 3 findings, 4 negatives

A split key is written with the fragment placeholders of eval/run.py (@@AKIA@@ + @@AKT1@@@@AKT2@@ is the AWS key), so no tracked file
holds a provider key or an expression that folds into one. Every id is checked against the holdout hash: these cases are
training cases, so `holdout_of(id)` must be False (force_holdout is not used).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import seed_v1 as v1                      # noqa: E402

F, PY_BASE, GO_BASE, TS_BASE = v1.F, v1.PY_BASE, v1.GO_BASE, v1.TS_BASE
ORIGIN = "round 3 authored from README rules (no tool output)"
AKIA, T1T2 = "@@AKIA@@", "@@AKT1@@@@AKT2@@"          # "AKIA" and "QWERTYUIOPASDFGH" (the tail of @@AWS@@)
BLOCK = ("block", "block")
# SLACK is the token every other case that writes a Slack token uses; it carries a documentation-placeholder run (digits 12345678, the
# alphabet), which the tool drops in every language. SLACKKEY has none: it is the token for the case that expects a BLOCK for a Slack
# token in a .properties file (eval/LABEL_CHANGES.md H, decided by the maintainer: the generator changes, the label does not).
TOKEN = {"AWS": "@@AWS@@", "GH": "@@GHTOKEN@@", "STRIPE": "@@STRIPE@@", "SLACK": "@@SLACK@@", "SLACKKEY": "@@SLACKKEY@@"}
PLAIN = {"python": PY_BASE, "go": GO_BASE, "typescript": TS_BASE, "javascript": TS_BASE}


def case(id, family, language, after, fire=(), notfire=(), before=None, **kw):
    assert not v1.holdout_of(id), f"{id} would be routed to eval/holdout (hash); pick another id"
    assert id.startswith("r3-")
    v1.emit(id, family, language, ORIGIN, PLAIN.get(language, PY_BASE) if before is None else before, after, fire, notfire, **kw)


def adv(id, language, after, fire=(), notfire=(), **kw):
    case(id, "adversarial", language, after, fire, notfire, **kw)


def seeded(id, language, after, fire=(), notfire=(), **kw):
    case(id, "seeded", language, after, fire, notfire, **kw)


# ----------------------------------------------------------------------------------------------- A1. secrets.hardcoded

def hardcoded():
    # ---- constant-fold shapes in JS, TS and Go (a provider key is spelled out, only in pieces)
    adv("r3-js-key-concat", "javascript", {"src/config.js": f'const awsKey = "{AKIA}" + "{T1T2}";\nmodule.exports = {{ awsKey }};\n'},
        [F("secrets.hardcoded", "src/config.js", "awsKey =", *BLOCK)])
    adv("r3-js-key-template-literal", "javascript", {"src/config.js": f'const awsKey = `${{"{AKIA}"}}{T1T2}`;\nmodule.exports = {{ awsKey }};\n'},
        [F("secrets.hardcoded", "src/config.js", "awsKey =", *BLOCK)])
    adv("r3-js-key-join", "javascript", {"src/config.js": f'const awsKey = ["{AKIA}", "{T1T2}"].join("");\nmodule.exports = {{ awsKey }};\n'},
        [F("secrets.hardcoded", "src/config.js", "awsKey =", *BLOCK)])
    adv("r3-ts-key-concat", "typescript", {"src/config.ts": f'export const awsKey: string = "{AKIA}" + "{T1T2}";\n'},
        [F("secrets.hardcoded", "src/config.ts", "awsKey", *BLOCK)])
    adv("r3-ts-key-template", "typescript", {"src/config.ts": f'export const awsKey = `${{"{AKIA}"}}{T1T2}`;\n'},
        [F("secrets.hardcoded", "src/config.ts", "awsKey", *BLOCK)])
    adv("r3-ts-key-join", "typescript", {"src/config.ts": f'export const awsKey = ["{AKIA}", "@@AKT1@@", "@@AKT2@@"].join("");\n'},
        [F("secrets.hardcoded", "src/config.ts", "awsKey", *BLOCK)])
    adv("r3-go-key-concat", "go", {"shop/config.go": f'package shop\n\nconst awsKey = "{AKIA}" + "{T1T2}"\n'},
        [F("secrets.hardcoded", "shop/config.go", "awsKey", *BLOCK)])
    adv("r3-go-key-sprintf-two", "go", {"shop/config.go": f'package shop\n\nimport "fmt"\n\nvar awsKey = fmt.Sprintf("%s%s", "{AKIA}", "{T1T2}")\n'},
        [F("secrets.hardcoded", "shop/config.go", "awsKey", *BLOCK)])

    # ---- a key in an unusual file or place
    ci = ("name: deploy\non: push\njobs:\n  charge:\n    runs-on: ubuntu-latest\n    steps:\n      - run: |\n"
          f"          curl -s -u {TOKEN['STRIPE']}: https://api.stripe.com/v1/balance\n")
    adv("r3-yaml-ci-key-in-curl", "yaml", {"ci/pipeline.yml": ci}, [F("secrets.hardcoded", "ci/pipeline.yml", "curl", *BLOCK)])
    adv("r3-docker-run-key", "dockerfile", {"Dockerfile": f"FROM python:3.12-slim\nRUN aws configure set aws_access_key_id {TOKEN['AWS']}\n"},
        [F("secrets.hardcoded", "Dockerfile", "aws configure", *BLOCK)])
    adv("r3-properties-key", "properties", {"src/main/resources/application.properties": f"app.name=shop\nnotify.slack.token={TOKEN['SLACKKEY']}\n"},
        [F("secrets.hardcoded", "src/main/resources/application.properties", "notify.slack.token", *BLOCK)])
    adv("r3-toml-key", "toml", {"config/deploy.toml": f'[registry]\nurl = "https://ghcr.io"\ntoken = "{TOKEN["GH"]}"\n'},
        [F("secrets.hardcoded", "config/deploy.toml", "token", *BLOCK)])
    nb = {"cells": [{"cell_type": "code", "execution_count": 1, "metadata": {}, "source": ["print('hello')"],
                     "outputs": [{"name": "stdout", "output_type": "stream", "text": [f"token: {TOKEN['GH']}\n"]}]}],
          "metadata": {}, "nbformat": 4, "nbformat_minor": 5}
    adv("r3-notebook-output-key", "json", {"notebooks/demo.ipynb": json.dumps(nb, indent=1) + "\n"},
        [F("secrets.hardcoded", "notebooks/demo.ipynb", "token:", *BLOCK)])
    adv("r3-markdown-key", "markdown", {"docs/deploy.md": f"# Deploy\n\nRun this once:\n\n```sh\ncurl -H \"Authorization: token {TOKEN['GH']}\" https://api.github.com/user\n```\n"},
        [F("secrets.hardcoded", "docs/deploy.md", "curl", *BLOCK)])

    # ---- look-alikes that are NOT keys: nothing may be reported
    adv("r3-ts-aws-docs-example-ok", "typescript", {"src/aws.ts": 'export const exampleKey = "AKIAIOSFODNN7EXAMPLE"; // the key from the AWS documentation\n'},
        notfire=[("secrets.hardcoded", "src/aws.ts")])
    adv("r3-yaml-fake-placeholder-ok", "yaml", {"deploy/values.yaml": f"aws_access_key_id: {AKIA}XXXXXXXXXXXXXXXX   # placeholder, set it in the secret store\n"},
        notfire=[("secrets.hardcoded", "deploy/values.yaml")])
    adv("r3-go-sha256-digest-ok", "go", {"shop/digest.go": 'package shop\n\nconst releaseDigest = "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"\n\n'
                                                           'const buildID = "3f2504e0-4f89-41d3-9a0c-0305e82c3301"\n'},
        notfire=[("secrets.hardcoded", "shop/digest.go")])
    adv("r3-js-join-words-ok", "javascript", {"src/names.js": 'const header = ["ACCESS", "KEY", "ID"].join("_");\nconst label = "Access" + " " + "granted";\nmodule.exports = { header, label };\n'},
        notfire=[("secrets.hardcoded", "src/names.js")])


# ----------------------------------------------------------------------------------------------- A2. review.claim_mismatch

def claims():
    CM = F("review.claim_mismatch", "(commit)", None, "warn", "warn")
    # claims the diff does not support
    seeded("r3-py-claim-add-tests-no-test", "python",
           {"tests/conftest.py": "import pytest\n\n\n@pytest.fixture\ndef items():\n    return [1, 2, 3]\n"}, [CM], messages=["add tests for slugify"])
    st = GO_BASE["store/store.go"]
    seeded("r3-go-claim-nil-check", "go",
           {"store/store.go": st.replace('import "os"', 'import (\n\t"os"\n\t"path/filepath"\n)').replace("os.ReadFile(p)", "os.ReadFile(filepath.Clean(p))")},
           [CM], messages=["fix the nil check in Load"])
    seeded("r3-ts-claim-remove-unused", "typescript",
           {"src/lib.ts": TS_BASE["src/lib.ts"] + "\nexport const triple = (x: number) => x * 3;\n"}, [CM], messages=["remove the unused helper function"])
    seeded("r3-py-claim-handle-the-error", "python",
           {"app/store.py": PY_BASE["app/store.py"] + "\n\ndef size(path):\n    import os\n    return os.path.getsize(path)\n"}, [CM],
           messages=["handle the error when the file is missing"])
    seeded("r3-go-claim-handle-error", "go",
           {"shop/read.go": 'package shop\n\nimport "os"\n\nfunc Read(p string) []byte {\n\tb, _ := os.ReadFile(p)\n\treturn b\n}\n'}, [CM],
           messages=["handle the error from ReadFile"])
    # true claims: no finding
    go_before = {**GO_BASE, "shop/describe.go": "package shop\n\ntype Item struct{ Name string }\n\nfunc Describe(p *Item) string {\n\treturn p.Name\n}\n"}
    seeded("r3-go-claim-true-nil-check", "go",
           {"shop/describe.go": "package shop\n\ntype Item struct{ Name string }\n\nfunc Describe(p *Item) string {\n\tif p == nil {\n\t\treturn \"\"\n\t}\n\treturn p.Name\n}\n"},
           notfire=[("review.claim_mismatch", "(commit)")], before=go_before, messages=["fix the nil check in Describe"])
    util = PY_BASE["app/util.py"]
    py_before = {**PY_BASE, "app/util.py": util + "\n\ndef legacy_format(x):\n    return str(x)\n"}
    seeded("r3-py-claim-true-remove-unused", "python", {"app/util.py": util},
           notfire=[("review.claim_mismatch", "(commit)")], before=py_before, messages=["remove the unused legacy_format helper"])


# ----------------------------------------------------------------------------------------------- A3. tests.skip_added boundaries

def skips():
    t = PY_BASE["tests/test_orders.py"]
    # a conditional skip on the platform, with a stated reason: environment-gated, a warning for a person; an agent is never softer
    seeded("r3-py-skipif-platform", "python",
           {"tests/test_orders.py": t.replace("def test_total", 'import sys\nimport pytest\n\n\n@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes only")\ndef test_total')},
           [F("tests.skip_added", "tests/test_orders.py", "skipif", "warn", "fix", tol=2)])
    # a skip that already existed (unchanged line) while the file gets a new case: nothing was skipped by this change
    tt = TS_BASE["src/lib.test.ts"].replace("test('empty'", "it.skip('empty'")
    seeded("r3-ts-skip-unchanged", "typescript",
           {"src/lib.test.ts": tt + "\ntest('single', () => {\n  expect(total([7], 0)).toBe(7);\n});\n"},
           notfire=[("tests.skip_added", "src/lib.test.ts")], before={**TS_BASE, "src/lib.test.ts": tt})
    # a skip REMOVED in the diff: the test runs again
    gt = GO_BASE["shop/shop_test.go"]
    skipped = gt.replace("func TestParse(t *testing.T) {\n", 'func TestParse(t *testing.T) {\n\tt.Skip("flaky on CI")\n')
    seeded("r3-go-skip-removed", "go", {"shop/shop_test.go": gt}, notfire=[("tests.skip_added", "shop/shop_test.go")],
           before={**GO_BASE, "shop/shop_test.go": skipped})
    # README: "skip/xfail added" is a fix; a strict xfail with a reason is still an expected failure added
    seeded("r3-py-xfail-strict-reason", "python",
           {"tests/test_orders.py": t.replace("def test_total", 'import pytest\n\n\n@pytest.mark.xfail(strict=True, reason="tracked in issue 412")\ndef test_total')},
           [F("tests.skip_added", "tests/test_orders.py", "pytest.mark.xfail", "fix", "fix", tol=2)])
    # a method called skip() in application code is not a test skip
    seeded("r3-py-skip-in-app-code", "python",
           {"app/jobs.py": 'def run(job, runner):\n    if job.get("done"):\n        runner.skip("already processed")\n        return None\n    return runner.run(job)\n'},
           notfire=[("tests.skip_added", "app/jobs.py")])


# ----------------------------------------------------------------------------------------------- B. secrets.generic (multi-word values)

def generic():
    G = lambda path, needle: F("secrets.generic", path, needle, "fix", "fix")
    seeded("r3-py-generic-multiword", "python", {"app/config.py": 'db_password = "correct horse battery staple"\n'}, [G("app/config.py", "db_password")])
    seeded("r3-ts-generic-multiword", "typescript", {"src/config.ts": 'export const apiSecret = "purple monkey dishwasher pass";\n'}, [G("src/config.ts", "apiSecret")])
    seeded("r3-go-generic-multiword", "go", {"shop/auth.go": 'package shop\n\nconst authToken = "tiger lily ocean breeze"\n'}, [G("shop/auth.go", "authToken")])
    seeded("r3-py-generic-sentence-punct", "python",
           {"app/messages.py": 'secret_hint = "The secret is stored in the vault."\ntoken_notice = "Your token has been rotated!"\npassword_prompt = "Is the password correct?"\n'},
           notfire=[("secrets.generic", "app/messages.py")])
    seeded("r3-ts-generic-short-value", "typescript", {"src/auth.ts": 'export const password = "blue horse";\n'}, notfire=[("secrets.generic", "src/auth.ts")])
    seeded("r3-go-generic-test-file", "go",
           {"shop/auth_test.go": 'package shop\n\nimport "testing"\n\nfunc TestLogin(t *testing.T) {\n\tpassword := "correct horse battery staple"\n\t_ = password\n}\n'},
           notfire=[("secrets.generic", "shop/auth_test.go")])
    seeded("r3-py-generic-ui-label", "python", {"app/forms.py": 'password_label = "Enter your password here"\n'}, notfire=[("secrets.generic", "app/forms.py")])


def main():
    hardcoded()
    claims()
    skips()
    generic()
    assert len(set(v1.CASES)) == len(v1.CASES) == 37, len(v1.CASES)
    print(len(v1.CASES), "round-3 cases written (all in eval/corpus/)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
