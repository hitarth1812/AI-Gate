#!/usr/bin/env python3
"""Corpus v2 (round 2, item 5): 15 more SEEDED cases (clean base + one injected AI failure), all written to eval/holdout/ so
the existing training cases do not move and the holdout rises to about 20% of the corpus. They use the same bases and helpers as
seed_v1.py. Every expectation is written from the intent of the injected mistake, never copied from what the tool printed.

    py eval/tools/seed_v2.py

Do not re-run seed_v1.py over a corpus that has v2 cases (it clears eval/corpus and eval/holdout except clean-*); run this one
after it if both are ever needed.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import seed_v1 as v1                      # noqa: E402

F, PY_BASE, GO_BASE, TS_BASE = v1.F, v1.PY_BASE, v1.GO_BASE, v1.TS_BASE


def main():
    H = {"force_holdout": True}
    P, G, T = "seed2-py-", "seed2-go-", "seed2-ts-"
    t = PY_BASE["tests/test_orders.py"]
    # ---- python
    v1.py(P + "trivial-assert", {"tests/test_orders.py": t.replace("    assert compute_total([10], 0.5) == 15\n", "    assert True\n")},
          [F("tests.trivial_assert", "tests/test_orders.py", "assert True", "fix", "fix")], **H)
    v1.py(P + "leak-slack", {"app/notify.py": "SLACK_TOKEN = \"@@SLACK@@\"\n"}, [F("secrets.hardcoded", "app/notify.py", "SLACK_TOKEN", "block", "block")], **H)
    v1.py(P + "halluc-import-typo", {"app/stats.py": "import numpyx\n\n\ndef mean(xs):\n    return numpyx.mean(xs)\n"},
          [F("deps.hallucinated", "app/stats.py", "import numpyx", "block", "block")], **H)
    v1.py(P + "eval-added", {"app/calc.py": "def run(expr):\n    return eval(expr)\n"}, [F("security.eval", "app/calc.py", "eval(", "fix", "fix")], **H)
    v1.py(P + "pickle-loads", {"app/cache.py": "import pickle\n\n\ndef load(blob):\n    return pickle.loads(blob)\n"},
          [F("security.deserialization", "app/cache.py", "pickle.loads", "fix", "fix")], **H)
    v1.py(P + "breakpoint-left", {"app/orders.py": PY_BASE["app/orders.py"].replace("    total = sum(items)\n", "    total = sum(items)\n    breakpoint()\n")},
          [F("code.debug_leftover", "app/orders.py", "breakpoint()", "fix", "fix")], **H)
    # ---- go
    v1.go(G + "leak-slack", {"shop/notify.go": "package shop\n\nconst slackToken = \"@@SLACK@@\"\n"},
          [F("secrets.hardcoded", "shop/notify.go", "slackToken", "block", "block")], **H)
    v1.go(G + "sql-concat", {"shop/db.go": "package shop\n\nimport \"database/sql\"\n\nfunc Find(db *sql.DB, id string) (*sql.Rows, error) {\n"
                             "\treturn db.Query(\"SELECT * FROM users WHERE id = \" + id)\n}\n"},
          [F("security.sql_injection", "shop/db.go", "db.Query", "fix", "fix")], **H)
    gt = GO_BASE["shop/shop_test.go"]
    v1.go(G + "skip-todo", {"shop/shop_test.go": gt.replace("func TestParse(t *testing.T) {\n", "func TestParse(t *testing.T) {\n\tt.Skip(\"TODO: fix later\")\n")},
          [F("tests.skip_added", "shop/shop_test.go", "t.Skip", "fix", "fix")], **H)
    v1.go(G + "tls-verify-off", {"shop/client.go": "package shop\n\nimport (\n\t\"crypto/tls\"\n\t\"net/http\"\n)\n\nfunc Client() *http.Client {\n"
                                 "\treturn &http.Client{Transport: &http.Transport{TLSClientConfig: &tls.Config{InsecureSkipVerify: true}}}\n}\n"},
          [F("security.verification_disabled", "shop/client.go", "InsecureSkipVerify", "fix", "fix")], **H)
    # ---- typescript
    tt = TS_BASE["src/lib.test.ts"]
    v1.ts(T + "describe-only", {"src/lib.test.ts": tt.replace("test('empty', () => {", "describe.only('empty', () => {")},
          [F("tests.focused", "src/lib.test.ts", "describe.only", "fix", "fix")], **H)
    pj = json.loads(TS_BASE["package.json"])
    v1.ts(T + "halluc-devdep", {"package.json": json.dumps({**pj, "devDependencies": {"jest-super-matchers-pro": "^2.0.0"}}, indent=2) + "\n"},
          [F("deps.hallucinated", "package.json", "jest-super-matchers-pro", "block", "block")], **H)
    v1.ts(T + "tls-verify-off", {"src/client.ts": "import https from 'https';\n\nexport const agent = new https.Agent({ rejectUnauthorized: false });\n"},
          [F("security.verification_disabled", "src/client.ts", "rejectUnauthorized", "fix", "fix")], **H)
    v1.ts(T + "eval-added", {"src/run.ts": "export const run = (s: string) => eval(s);\n"}, [F("security.eval", "src/run.ts", "eval(", "fix", "fix")], **H)
    # the file is made at run time (a stored .env.local would be a finding of this repository's own self-scan)
    v1.ts(T + "env-local-secret", {}, [F("secrets.env_file", ".env.local", None, "block", "block")],
          extra='[[generate]]\npath = ".env.local"\nkind = "text"\ntext = "API_SECRET=Zx9kQ2vB7mN4pL1w\\n"', **H)
    print(len(v1.CASES), "seeded v2 cases written (all in eval/holdout/)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
