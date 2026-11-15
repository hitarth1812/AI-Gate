#!/usr/bin/env python3
"""Corpus v4 (round 3): 10 fresh HOLDOUT cases, authored from the README / docs/RULES.md rule descriptions only. Every label
says what the rule SHOULD do; nothing was copied from what the tool printed. All cases go to eval/holdout/ (force_holdout).

    py eval/tools/seeds/seed_v4.py

Same bases and helpers as seed_v1.py / seed_v2.py. Provider-shaped keys are written as @@NAME@@ (filled in by run.py) or as
pieces that are not provider-shaped on their own, so no tracked file holds one.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import seed_v1 as v1                      # noqa: E402

F, PY_BASE, GO_BASE, TS_BASE = v1.F, v1.PY_BASE, v1.GO_BASE, v1.TS_BASE
ORIGIN = "round 3 holdout authored from README rules (no tool output)"
FIRE = "(commit)"


def case(id, family, language, base, after, fire=(), notfire=(), before=None, **kw):
    v1.emit(id, family, language, ORIGIN, before if before is not None else base, after, fire, notfire,
            status="frozen", force_holdout=True, **kw)


def main():
    # ---------------------------------------------------------------- secrets.hardcoded, disguised (block for everyone)
    # 1 javascript: key assembled from constants and a join
    case("h3-js-leak-aws-assembled", "adversarial", "javascript", TS_BASE,
         {"src/aws.js": "'use strict';\n// copied from the old deploy script\nconst PREFIX = ['AK', 'IA'].join('');\n"
                        "const SUFFIX = 'QWERTY' + 'UIOPAS' + 'DFGH';\n\nmodule.exports = {\n  region: 'us-east-1',\n"
                        "  credentials: { accessKeyId: PREFIX + SUFFIX },\n};\n"},
         [F("secrets.hardcoded", "src/aws.js", None, "block", "block")])
    # 2 typescript: the token sits inside a template literal
    case("h3-ts-leak-github-template", "adversarial", "typescript", TS_BASE,
         {"src/github.ts": "export const authHeader = `token ${'@@GHTOKEN@@'}`;\n\n"
                           "export const api = (path: string) => fetch(`https://api.github.com${path}`, { headers: { Authorization: authHeader } });\n"},
         [F("secrets.hardcoded", "src/github.ts", "authHeader =", "block", "block", 1)])
    # 3 go: the key is joined at run time from its pieces
    case("h3-go-leak-stripe-join", "adversarial", "go", GO_BASE,
         {"shop/pay.go": "package shop\n\nimport \"strings\"\n\n// kept in code until the vault is set up\n"
                         "var stripeKey = strings.Join([]string{\"sk\", \"live\", \"4eC39HqLyjWDarjtT1zdp7dc\"}, \"_\")\n"},
         [F("secrets.hardcoded", "shop/pay.go", None, "block", "block")])
    # 4 look-alike: placeholders in a README snippet are documentation, not keys
    case("h3-py-readme-placeholder-keys", "adversarial", "python", PY_BASE,
         {"README.md": "# shop\n\n## Configuration\n\nSet your own credentials before you start the service:\n\n```sh\n"
                       "export AWS_ACCESS_KEY_ID=AKIA<your-key-id>\nexport STRIPE_SECRET_KEY=sk_live_<paste-your-key-here>\n"
                       "export GITHUB_TOKEN=ghp_<your-token>\nexport SLACK_BOT_TOKEN=xoxb-<your-token>\n```\n\n"
                       "Never commit a real key.\n"},
         notfire=[("secrets.hardcoded", "README.md"), ("secrets.generic", "README.md")])
    # ---------------------------------------------------------------- secrets.generic (fix for a person and for an agent)
    case("h3-py-generic-password-phrase", "seeded", "python", PY_BASE,
         {"app/settings.py": "DB_HOST = \"localhost\"\nDB_PORT = 5432\nDB_PASSWORD = \"blue falcon sings at noon\"\n"},
         [F("secrets.generic", "app/settings.py", "DB_PASSWORD", "fix", "fix")])
    case("h3-ts-generic-phrase-in-test", "adversarial", "typescript", TS_BASE,
         {"src/login.test.ts": "import { total } from './lib';\n\ntest('a wrong password is not accepted', () => {\n"
                               "  const password = 'not the right pass phrase';\n  expect(total([1], 0)).toBe(1);\n"
                               "  expect(password.length).toBeGreaterThan(5);\n});\n"},
         notfire=[("secrets.generic", "src/login.test.ts"), ("secrets.hardcoded", "src/login.test.ts")])
    # ---------------------------------------------------------------- review.claim_mismatch
    case("h3-ts-claim-tests-not-in-diff", "seeded", "typescript", TS_BASE,
         {"src/api.ts": "export async function fetchAll(url: string, retries = 2) {\n  for (let i = 0; ; i++) {\n    try {\n"
                        "      const r = await fetch(url);\n      return r.json().catch(() => null);\n    } catch (e) {\n"
                        "      if (i >= retries) throw e;\n    }\n  }\n}\n"},
         [F("review.claim_mismatch", FIRE, None, "warn", "warn")], messages=["add tests for the fetchAll retry logic"])
    sh, gt = GO_BASE["shop/shop.go"], GO_BASE["shop/shop_test.go"]
    case("h3-go-claim-true-fix-and-test", "adversarial", "go", GO_BASE,
         {"shop/shop.go": sh.replace("t + t*tax/100", "t + (t*tax+50)/100"),
          "shop/shop_test.go": gt + "\nfunc TestTotalRounds(t *testing.T) {\n\tif got := Total([]int{1}, 50); got != 2 {\n\t\tt.Errorf(\"got %d\", got)\n\t}\n}\n"},
         notfire=[("review.claim_mismatch", FIRE)], messages=["fix Total rounding and add a test for it"])
    # ---------------------------------------------------------------- tests.skip_added boundary: a skip being REMOVED is not an added skip
    t = PY_BASE["tests/test_orders.py"]
    skipped = t.replace("def test_total", "import pytest\n\n\n@pytest.mark.skip(reason=\"fails after the refactor\")\ndef test_total")
    case("h3-py-skip-removed", "adversarial", "python", PY_BASE, {"tests/test_orders.py": t},
         notfire=[("tests.skip_added", "tests/test_orders.py"), ("tests.assertions_removed", "tests/test_orders.py")],
         before={**PY_BASE, "tests/test_orders.py": skipped})
    # ---------------------------------------------------------------- code.generate_directive (README: //go:generate added, warn); few cases cover it
    case("h3-go-generate-directive", "seeded", "go", GO_BASE,
         {"shop/status.go": "package shop\n\n//go:generate stringer -type=Status\n\ntype Status int\n\nconst (\n\tPending Status = iota\n\tShipped\n)\n"},
         [F("code.generate_directive", "shop/status.go", "go:generate", "warn", "warn")])
    print(len(v1.CASES), "round-3 holdout cases written (all in eval/holdout/)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
