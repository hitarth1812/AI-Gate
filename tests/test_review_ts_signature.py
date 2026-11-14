"""review.claim_mismatch: a changed TypeScript/JavaScript function signature contradicts "refactor only", as it does
for Python and Go. The finding is a warn for a person and for an agent alike; api.broken_caller stays Python/Go only."""
import pytest

from test_review_api import AGENT, base, change, deep
from test_review_rest import claims

LIB = "export function total(items: number[], tax: number): number {\n  return items.length + tax;\n}\n"
CLAIM = "refactor only, no behaviour change"


def _sig_claims(repo, env=None):
    return [c for c in claims(repo, CLAIM, env=env) if "signature" in c[0]]


@pytest.mark.parametrize("env", [None, AGENT], ids=["person", "agent"])
@pytest.mark.parametrize("path", ["src/lib.ts", "src/lib.js"])
def test_a_ts_signature_change_contradicts_a_refactor_claim(repo, env, path):
    lib = LIB if path.endswith(".ts") else "export function total(items, tax) {\n  return items.length + tax;\n}\n"
    base(repo, {path: lib})
    change(repo, {path: lib.replace("tax)", "tax, round)").replace("tax: number)", "tax: number, round: boolean)")})
    got = _sig_claims(repo, env)
    assert len(got) == 1 and "`total`" in got[0][0] and got[0][1] == "warn"


def test_a_ts_arrow_export_signature_change_is_reported(repo):
    text = "export const total = (items: number[], tax: number): number => items.length + tax;\n"
    base(repo, {"src/lib.ts": text})
    change(repo, {"src/lib.ts": text.replace("tax: number)", "tax: number, round: boolean)")})
    assert len(_sig_claims(repo)) == 1


def test_an_unchanged_ts_signature_is_not_contradicted(repo):
    base(repo, {"src/lib.ts": LIB})
    change(repo, {"src/lib.ts": LIB.replace("items.length + tax", "tax + items.length")})
    assert not claims(repo, CLAIM)


def test_an_added_optional_ts_parameter_is_reported_like_python_and_go(repo):
    base(repo, {"src/lib.ts": LIB})
    change(repo, {"src/lib.ts": LIB.replace("tax: number)", "tax: number, round?: boolean)")})
    assert len(_sig_claims(repo)) == 1


def test_a_ts_signature_change_does_not_raise_broken_caller(repo):
    base(repo, {"src/lib.ts": LIB, "src/app.ts": "import { total } from './lib';\nexport const x = total([1], 2);\n"})
    change(repo, {"src/lib.ts": LIB.replace("tax: number)", "tax: number, round: boolean)")})
    for env in (None, AGENT):
        assert not [f for f in deep(repo, env)["findings"] if f["rule"] == "api.broken_caller"]
