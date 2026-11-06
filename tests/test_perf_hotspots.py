"""Phase 4A item 13: the two speed-ups made after profiling `check --all` must not change what is found."""
import string

from conftest import aigate

ALNUM = (string.ascii_letters + string.digits) * 4


def body(n):
    return ALNUM[:n]


SAMPLES = {
    "AWS access key ID": ["AKIA" + "ABCDEFGHIJKLMNOP", "ASIA" + "ABCDEFGHIJKLMNOP"],
    "AWS secret access key": ['aws_secret_access_key = "' + body(40) + '"'],
    "GitHub token": ["ghp_" + body(36), "ghs_" + body(40)],
    "GitHub fine-grained token": ["github_pat_" + body(70)],
    "GitLab token": ["glpat-" + body(22)],
    "Slack token": ["xoxb-" + "1234567890-abcdef"],
    "Slack webhook": ["https://hooks.slack.com/services/T0000/B0000/" + body(24)],
    "Discord webhook": ["https://discord.com/api/webhooks/123456789/" + body(34), "https://discordapp.com/api/webhooks/1/" + body(34)],
    "Stripe live key": ["sk_live_" + body(24), "rk_live_" + body(24)],
    "Google API key": ["AIza" + body(35)],
    "Google OAuth client secret": ["GOCSPX-" + body(28)],
    "OpenAI API key": ["sk-proj-" + body(48), "sk-" + body(20) + "T3BlbkFJ" + body(20)],
    "Anthropic API key": ["sk-ant-api03-" + body(64)],
    "Hugging Face token": ["hf_" + body(36)],
    "npm token": ["npm_" + body(36)],
    "PyPI token": ["pypi-AgEIcHlwaS5vcmc" + body(60)],
    "SendGrid API key": ["SG." + body(22) + "." + body(43)],
    "Telegram bot token": ["123456789:AA" + body(33)],
    "Shopify token": ["shpat_" + "0123456789abcdef" * 2],
    "DigitalOcean token": ["dop_v1_" + "0123456789abcdef" * 4],
    "Azure storage key": ["AccountKey=" + body(86) + "=="],
    "Private key": ["-----BEGIN " + "RSA PRIVATE KEY-----"],
}


def test_every_provider_pattern_has_a_sample_and_the_prefilter_admits_it():
    for label, rx in aigate.PROVIDER_SECRETS:
        matching = [s for s in SAMPLES[label] if rx.search(s)]
        assert matching, f"no sample for {label}: {rx.pattern[:40]}"
        for sample in matching:
            assert aigate.PROVIDER_LITERALS.search(sample), f"the prefilter would hide {label}"
    assert {label for label, _ in aigate.PROVIDER_SECRETS} == set(SAMPLES)


def test_the_prefilter_rejects_ordinary_code():
    for line in ("func main() { fmt.Println(\"hello\") }", "x := compute(a, b)", "    return nil", "// plain comment"):
        assert not aigate.PROVIDER_LITERALS.search(line)


def test_cached_path_flags_equal_the_uncached_answers():
    cfg = aigate.load_config.__globals__["DEFAULT_CONFIG"]
    paths = ["a_test.go", "pkg/a.go", "tests/x.py", "vendor/x/y.go", "node_modules/z/index.js", "src/app.test.ts",
             "docs/readme.md", "svc/m_test.go", "test_thing.py"]

    class Ctx(aigate.Context):
        def __init__(self):
            self.cfg = {"paths": {"ignore": cfg["paths"]["ignore"], "tests": cfg["paths"]["tests"]}}
            self._path_flags = {}

    ctx = Ctx()
    for path in paths * 2:                                        # the second pass answers from the memo
        spec = aigate.language_for(path)
        expect_test = aigate.path_matches(path, cfg["paths"]["tests"]) or bool(
            spec and spec.tests and aigate.path_matches(path, spec.tests))
        assert ctx.ignored(path) == aigate.path_matches(path, cfg["paths"]["ignore"]), path
        assert ctx.is_test(path) == expect_test, path
