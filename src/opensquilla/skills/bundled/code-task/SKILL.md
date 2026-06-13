---
name: code-task
description: "Solve a coding task in a REAL git repository — fix a GitHub issue or implement a user's feature request — by running an OpenSquilla agent on the host, then verifying the change with a red→green→regression test loop. Trigger when the user wants to fix an issue in a repo, implement a feature/change in a codebase, or '解决某仓库的 issue / 给某项目加个功能'. Needs the docker-free host: clones the repo, runs the agent, runs tests. Treat the target repo as TRUSTED (host execution, not a sandbox). GitHub issue mode needs the `gh` CLI."
triggers:
  - "code-task"
  - "fix the issue"
  - "fix issue"
  - "implement this feature"
  - "解决 issue"
  - "给项目加功能"
  - "修复仓库"
provenance:
  origin: opensquilla-original
  license: Apache-2.0
  maintained_by: OpenSquilla
metadata:
  {
    "platform":
      {
        "emoji": "🛠️",
        "requires": { "bins": ["git"], "env": ["OPENROUTER_API_KEY"] },
        "install": [],
      },
  }
---

# code-task

Solve a real-repository coding task end to end: clone the repo to a
disposable working directory, run an OpenSquilla agent to make the change on
a task branch, then **independently verify** it with a red→green→regression
loop. Host mode (no Docker) in v1.

## Translating the user's request

The user speaks naturally ("fix issue 412 in github.com/acme/widgets",
"add CSV BOM support to my project at ~/code/foo"). Map that to the command:

```
opensquilla code-task solve --repo <url-or-path> ( --issue N | --task "<text>" | --task-file <path> ) [--yes]
```

- **A GitHub issue** → `--issue N` (needs `gh`; see below).
- **A short request in the message** → `--task "<their request>"`.
- **A long spec, or pasted from Jira/GitLab/内网** → save it to a file and
  use `--task-file <path>`.
- Always pass `--repo`. If the user is already in a local checkout, use that
  path; otherwise the GitHub/remote URL.
- Pass `--yes` to skip the interactive trusted-host confirmation (you are
  acting on the user's behalf), but only after the safety check below.

## Before you run — two checks

1. **Trusted repo**: code-task runs an agent on the host that may install
   dependencies and execute the repo's code. It is NOT a sandbox. Only run
   it against repositories the user trusts. If the repo's provenance is
   unclear, ask first.
2. **Enough information**: you must be able to state the expected behavior
   change ("what is wrong/missing now, what should be true after"). If the
   request is too vague to write an acceptance test for, ask the user to
   clarify BEFORE running — do not burn a run on a guess.

## GitHub issue mode needs `gh`

`--issue` shells out to the GitHub CLI (`gh`). If `gh` is missing or not
authenticated, tell the user to `gh auth login`, or fall back: have them
paste the issue text and use `--task` / `--task-file` instead. The issue
body AND comments are pulled in (comments often hold the repro steps).

## Reading the result

`--json` prints a result object; key fields:

- `state`: `verified` (acceptance test went red→green, no regressions),
  `already_satisfied` (the behavior already held on the base commit),
  `not_testable` (work done but not expressible as a test),
  `environment_blocked` (could not build/test the repo),
  `invalid_acceptance_test` (agent produced no valid verification manifest),
  `failed` (acceptance not green or a regression appeared).
- `branch`, `commits`, `files_changed`, `diffstat`, `patch_path`.
- `acceptance`: each test with `before`→`after` (e.g. `fail`→`pass`).
- `regression`: existing-suite result and `new_failures`.
- `assumptions`: **surface these to the user** — a wrong assumption means a
  wrong fix.
- `usage`: cost / tokens.

## What to tell the user

1. Up front: cloning + dependency install + the agent loop can take several
   minutes; you'll report when done.
2. After: report `state`, what changed (diffstat), the acceptance red→green
   evidence, any `assumptions`, the cost, and where the branch/diff lives.
3. On `failed` / `environment_blocked`: quote `error` and point at the
   `agent_stdout.log` under the artifact dir.

## Constraints

- Runs on the gateway host — git, the toolchain, and disk all come from
  there. Works the same from TUI, Web UI, or any channel.
- v1 is host-only and always clones fresh (no `--in-place`). For untrusted
  repositories, a Docker-isolated backend is planned but not in v1.
