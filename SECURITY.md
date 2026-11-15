# Security policy

## Reporting a vulnerability

Please do not open a public issue for a vulnerability.

1. **Preferred:** use GitHub's private vulnerability reporting:
   <https://github.com/hitarth1812/AI-Gate/security/advisories/new>
2. **Fallback:** email hitarthkhatiwala@gmail.com with the subject `aigate-lite security`.

What helps most in a report:

- the version (`aigate-lite --version`), your operating system and your Python version;
- a minimal reproducer (a few lines of a repository, a config file or a diff is enough);
- the impact, in one of these terms: a bypass of a BLOCK, code execution, or secret disclosure.

We aim to acknowledge a report within 7 days. This project has no bug bounty.

## Supported versions

Only the latest minor release gets security fixes (today: 0.6.x). Please check that the problem is still present in the latest
release before you report it.

## In scope

- a way for an AI agent's commit to pass a BLOCK that a person's commit would get;
- code execution from a cloned repository's configuration (for example through `.aigate/lite.toml` or `[[commands]]`);
- a secret printed un-redacted in any output format.

## Out of scope

- `git commit --no-verify` and other ways to skip the hook. This is a documented limit (see "Limits" in the README): the hook is a
  guard rail for the usual path, not a sandbox;
- false positives and missed detections that are not a bypass: please use the
  [false-positive issue template](https://github.com/hitarth1812/AI-Gate/issues/new?template=false_positive.yml) and
  [docs/FALSE_POSITIVES.md](docs/FALSE_POSITIVES.md).
