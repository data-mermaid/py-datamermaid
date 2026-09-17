# Agent Instructions

This project uses **bd** (beads) for issue tracking. Run `bd prime` for full workflow context.

> **Architecture in one line:** Issues live in a local Dolt database
> (`.beads/dolt/`); cross-machine sync uses `bd dolt push/pull` (a
> git-compatible protocol), stored under `refs/dolt/data` on your git
> remote — separate from `refs/heads/*` where your code lives.
> `.beads/issues.jsonl` is a passive export, not the wire protocol.
>
> See [SYNC_CONCEPTS.md](https://github.com/gastownhall/beads/blob/main/docs/SYNC_CONCEPTS.md)
> for the one-screen overview and anti-patterns (don't treat JSONL as the
> source of truth; don't `bd import` during normal operation; don't
> reach for third-party Dolt hosting before trying the default).

## Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --claim  # Claim work atomically
bd close <id>         # Complete work
bd dolt push          # Push beads data to remote
```

## Non-Interactive Shell Commands

**ALWAYS use non-interactive flags** with file operations to avoid hanging on confirmation prompts.

Shell commands like `cp`, `mv`, and `rm` may be aliased to include `-i` (interactive) mode on some systems, causing the agent to hang indefinitely waiting for y/n input.

**Use these forms instead:**
```bash
# Force overwrite without prompting
cp -f source dest           # NOT: cp source dest
mv -f source dest           # NOT: mv source dest
rm -f file                  # NOT: rm file

# For recursive operations
rm -rf directory            # NOT: rm -r directory
cp -rf source dest          # NOT: cp -r source dest
```

**Other commands that may prompt:**
- `scp` - use `-o BatchMode=yes` for non-interactive
- `ssh` - use `-o BatchMode=yes` to fail instead of prompting
- `apt-get` - use `-y` flag
- `brew` - use `HOMEBREW_NO_AUTO_UPDATE=1` env var

## Build & Test

This project is managed with [uv](https://docs.astral.sh/uv/) and targets Python 3.10+.

```bash
uv sync --all-extras       # create .venv and install runtime, extra and dev deps
uv run pytest              # run the test suite (no network: all HTTP is mocked with respx)
uv run ruff check .        # lint
uv run ruff format .       # format (use --check in CI)
uv run mypy src            # type check
```

Run one test file or a single test:

```bash
uv run pytest tests/test_pagination.py
uv run pytest -k "lazy"
```

CI (`.github/workflows/ci.yml`) runs ruff, mypy, and pytest on Python 3.10-3.13.

## Architecture Overview

`datamermaid` is a thin, typed SDK over the MERMAID REST API
(`https://api.datamermaid.org/v1/`, overridable with `base_url=` or
`MERMAID_API_URL`). Source lives in `src/datamermaid/`:

- `client.py`: `MermaidClient` wraps an `httpx.Client`; owns the base URL,
  timeouts, the `User-Agent`, retry-with-backoff on 429/5xx, and JSON decoding.
  Resources are reached through it (`client.projects`, `client.me()`).
- `auth/`: the `Auth` abstraction (`apply`/`refresh`/`should_refresh`) and its
  implementations. The client only ever calls `Auth`, so new schemes are added
  here, not in `client.py`.
  - `base.py`: `Auth`, `APIKeyAuth`, `AnonymousAuth`.
  - `oauth.py`: `OAuth`, the flow selection, and `login()`/`logout()`.
  - `flows.py`: the grants (`PkceFlow`, `ImplicitFlow`, `DeviceFlow`,
    `ManualPasteFlow`) behind a `FlowContext` holding every outside-world seam.
  - `config.py`: `Auth0Config` (kwargs > env vars > defaults).
  - `callback_server.py`: the single-shot loopback redirect server.
  - `token_cache.py` and `jwt.py`: the 0600 token file and the unverified
    `exp` decode that drives refresh.
- `exceptions.py`: `MermaidError` hierarchy and the status-code mapping.
- `models.py`: frozen dataclasses. `APIModel.from_api()` fills declared fields
  and keeps everything else in `extra`.
- `pagination.py`: `PaginatedList`, a lazy, caching view over DRF-style list
  responses, plus `to_df()`.
- `resources/`: one module per endpoint group, all extending `Resource`.

## Conventions & Patterns

- Everything is fully type annotated; `src/datamermaid/py.typed` ships the marker.
  `mypy` runs with `disallow_untyped_defs`.
- New endpoints: add a `Resource` subclass in `resources/` with `path` and
  `model`, and build on `_list()` / `_get()`. Expose it as a cached property on
  `MermaidClient`.
- New models: subclass `APIModel`, declare optional fields with defaults, and use
  `field(metadata=_api_meta(...))` for renames or value converters. Never make a
  field required; the API may omit it.
- Tests never touch the network. Mock with `respx` and assert on call counts when
  laziness matters.

<!-- BEGIN BEADS INTEGRATION v:1 profile:minimal hash:6cd5cc61 -->
## Beads Issue Tracker

This project uses **bd (beads)** for issue tracking. Run `bd prime` to see full workflow context and commands.

### Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --claim  # Claim work
bd close <id>         # Complete work
```

### Rules

- Use `bd` for ALL task tracking — do NOT use TodoWrite, TaskCreate, or markdown TODO lists
- Run `bd prime` for detailed command reference and session close protocol
- Use `bd remember` for persistent knowledge — do NOT use MEMORY.md files

**Architecture in one line:** issues live in a local Dolt DB; sync uses `refs/dolt/data` on your git remote; `.beads/issues.jsonl` is a passive export. See https://github.com/gastownhall/beads/blob/main/docs/SYNC_CONCEPTS.md for details and anti-patterns.

## Agent Context Profiles

The managed Beads block is task-tracking guidance, not permission to override repository, user, or orchestrator instructions.

- **Conservative (default)**: Use `bd` for task tracking. Do not run git commits, git pushes, or Dolt remote sync unless explicitly asked. At handoff, report changed files, validation, and suggested next commands.
- **Minimal**: Keep tool instruction files as pointers to `bd prime`; use the same conservative git policy unless active instructions say otherwise.
- **Team-maintainer**: Only when the repository explicitly opts in, agents may close beads, run quality gates, commit, and push as part of session close. A current "do not commit" or "do not push" instruction still wins.

## Session Completion

This protocol applies when ending a Beads implementation workflow. It is subordinate to explicit user, repository, and orchestrator instructions.

1. **File issues for remaining work** - Create beads for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **Handle git/sync by active profile**:
   ```bash
   # Conservative/minimal/default: report status and proposed commands; wait for approval.
   git status

   # Team-maintainer opt-in only, unless current instructions forbid it:
   git pull --rebase
   git push
   git status
   ```
5. **Hand off** - Summarize changes, validation, issue status, and any blocked sync/commit/push step

**Critical rules:**
- Explicit user or orchestrator instructions override this Beads block.
- Do not commit or push without clear authority from the active profile or the current user request.
- If a required sync or push is blocked, stop and report the exact command and error.
<!-- END BEADS INTEGRATION -->

<!-- BEGIN BEADS CODEX SETUP: generated by bd setup codex -->
## Beads Issue Tracker

Use Beads (`bd`) for durable task tracking in repositories that include it. Use the `beads` skill at `.agents/skills/beads/SKILL.md` (project install) or `~/.agents/skills/beads/SKILL.md` (global install) for Beads workflow guidance, then use the `bd` CLI for issue operations.

### Quick Reference

```bash
bd ready                # Find available work
bd show <id>            # View issue details
bd update <id> --claim  # Claim work
bd close <id>           # Complete work
bd prime                # Refresh Beads context
```

### Rules

- Use `bd` for all task tracking; do not create markdown TODO lists.
- Run `bd prime` when Beads context is missing or stale. Codex 0.129.0+ can load Beads context automatically through native hooks; use `/hooks` to inspect or toggle them.
- Keep persistent project memory in Beads via `bd remember`; do not create ad hoc memory files.

**Architecture in one line:** issues live in a local Dolt DB; sync uses `refs/dolt/data` on your git remote; `.beads/issues.jsonl` is a passive export. See https://github.com/gastownhall/beads/blob/main/docs/SYNC_CONCEPTS.md for details and anti-patterns.
<!-- END BEADS CODEX SETUP -->
