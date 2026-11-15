# Credit and usage

Every place the tool names itself is visible text, documented here, and never changes a result or an exit code.

| Where | What you see | When it is absent |
|---|---|---|
| `check` / `audit` text report | one last line: `aigate-lite by Hitarth Khatiwala · Apache-2.0 · <url>` (ASCII separators on a console that cannot show `·`) | on a clean pass, with `-q`, and in `--format json`, `agent`, `sarif` and `github` |
| `review` text report | the same line, always | never absent |
| `review --format md` | a last line `Reviewed by [aigate-lite](<url>) <version>` | never absent |
| JSON (`check`, `audit`, `review`) | `"author"` and `"url"` next to `"tool"` and `"version"` (`"schema"` is unchanged; nothing was removed or retyped) | |
| `--format agent` | one line `TOOL aigate-lite <version> <url>` directly before `END` | |
| SARIF | the tool driver's `name`, `version`, `informationUri` and `organization` | |

The credit is credit, not proof: nothing in the tool or in the CI examples trusts it, and nothing is hidden (there are no
invisible characters or encoded marks anywhere; a test scans the repository for them). The footer is a plain line of
text; the tool is Apache-2.0, so keep the copyright header and NOTICE when you redistribute it (Section 4).
