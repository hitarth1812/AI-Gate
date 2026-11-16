# AI agents

When an AI agent runs `git commit` itself, the gate notices it and becomes stricter. It detects agents
through `CLAUDECODE`, `AI_AGENT`, `GEMINI_CLI` or `AIGATE_AGENT` in the environment, an `(aider)`
author name, or AI co-author trailers in pushed commits. You can add your own variables under
`[provenance] agent_env`. For agent commits:

- `forbidden_for_agents` paths are blocked: `.aigate/`, CI workflows and hook configs.
- The config is read from the last commit, so an agent cannot loosen the gate in the same commit.
- `AIGATE_ALLOW` and `AIGATE_SKIP` are ignored.
- These commands refuse to run (exit 1, "refuses to run for an AI agent"), because each one would let an agent loosen or remove
  its own gate: `baseline`, `trust`, `scope set`, `scope add`, `scope clear` and `uninstall`. `scope show` still works. When a
  finding is outside the scope, the agent is told to stop and ask the human to widen it.

Detection is best effort. An agent that runs with none of these signals looks like a human.

## Using with AI coding agents

`--format agent` prints a short, ASCII-only, stable report an agent can act on line by line:

    AIGATE verdict=fix risk=30 exit=1
    FIX security.eval app/a.py:7 eval() runs code built at runtime | fix: Avoid eval/exec; parse the input explicitly (ast.literal_eval, JSON.parse).
    TOOL aigate-lite 0.5.0 https://github.com/hitarth1812/AI-Gate
    END

At most 50 block/fix lines and 20 warn lines are listed; a `MORE <n>` line says how many were left out.
The first line and `END` are the contract; `TOOL` (which tool wrote the report) always sits directly before `END`
and a reader may ignore it.
Add instructions like these to your agent's instruction file (`CLAUDE.md`, a `.cursor/rules` file,
`.github/copilot-instructions.md`, …):

```text
Before every commit, run: aigate-lite check --format agent
(in a repository with a vendored copy: python3 .aigate/aigate_lite.py check --format agent; on Windows: py .aigate/aigate_lite.py check --format agent)
Fix every BLOCK and FIX line it prints, then run it again until the first line says exit=0.
Never use `git commit --no-verify` and never edit files under .aigate/ (the gate's config and baseline).
If a finding looks wrong, stop and tell the user instead of working around it.
```

These instructions ask; the hooks enforce. Keep the hooks installed (`aigate-lite install`) and run the gate in
CI, because an agent can ignore instructions.
