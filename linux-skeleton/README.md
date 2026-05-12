# Linux Skeleton

This directory contains a runnable Phase 0 reference skeleton for the OpenCode-first Linux/Wayland flow described in:

- `.sisyphus/plans/opencode-first-niri-dms-plan.md`
- `.sisyphus/plans/phase-0-opencode-contract.md`

The current environment does not provide a Swift toolchain, so this skeleton is implemented in Python as a contract-aligned reference runtime. It is intentionally small and focused on validating:

- normalized event ingestion
- in-memory daemon state transitions
- provider/source identity preservation for OpenCode, Codex, and future adapters
- Unix socket RPC/subscription flow
- synthetic OpenCode fixture input
- mapped OpenCode hook-stream adapter input
- snapshot and patch delivery to a minimal subscriber

## Layout

- `codeisland_linux/protocol.py` — canonical Phase 0 entities and JSON envelopes
- `codeisland_linux/store.py` — in-memory daemon state and event application rules
- `codeisland_linux/server.py` — Unix socket daemon skeleton
- `codeisland_linux/fixture.py` — synthetic OpenCode-like event producer
- `codeisland_linux/opencode_plugin.py` — installer for the Linux OpenCode live plugin
- `codeisland_linux/opencode_adapter.py` — DB backfill/sync plus replay adapter for CodeIsland-style OpenCode hook events
- `codeisland_linux/codex_adapter.py` — live/replay adapter for Codex CLI rollout JSONL sessions
- `codeisland_linux/codex_hook.py` — optional Codex hook bridge and project hooks installer
- `codeisland_linux/claude_adapter.py` — live/replay adapter for Claude Code project/transcript JSONL sessions
- `codeisland_linux/claude_hook.py` — Claude Code hook bridge and settings installer
- `codeisland_linux/subscriber.py` — minimal snapshot/patch subscriber CLI
- `tests/` — unit and integration tests

The adapter also contains the first minimal response bridge for Phase 1: it can subscribe to daemon `interaction.resolved` events and map approval decisions back into a provider-facing reply payload.

## Run

Start the daemon:

```bash
python3 -m codeisland_linux.server
```

Run the happy-path fixture:

```bash
python3 -m codeisland_linux.fixture --scenario happy-path
```

Run a Codex-branded synthetic fixture:

```bash
python3 -m codeisland_linux.fixture --scenario codex
```

Populate the grouped DMS board with synthetic OpenCode, Codex, Claude, and Gemini sessions:

```bash
python3 -m codeisland_linux.fixture --scenario board-demo
```

Replay a semi-real OpenCode hook stream fixture:

```bash
python3 -m codeisland_linux.opencode_adapter --input tests/fixtures/opencode_hook_happy_path.jsonl
```

Install the preferred live OpenCode plugin:

```bash
python3 -m codeisland_linux.opencode_plugin install --backup
```

This writes `~/.config/opencode/plugins/codeisland-linux.js` and merges a
`file://.../codeisland-linux.js` entry into
`~/.config/opencode/opencode.json` while preserving existing plugins. Restart
OpenCode after installing. The plugin talks directly to the CodeIsland daemon
socket, forwards live session/message/tool events, opens daemon
approval/question cards, and replies back to OpenCode when the daemon resolves
the interaction.

Replay recent real OpenCode sessions from the local OpenCode SQLite database as
a backfill/recovery path:

```bash
python3 -m codeisland_linux.opencode_adapter
```

Keep following OpenCode's local database and hide stale DB-imported sessions
after the active window expires:

```bash
python3 -m codeisland_linux.opencode_adapter --watch
```

The OpenCode DB adapter reads `${OPENCODE_DB}` when set, otherwise
`${XDG_DATA_HOME:-~/.local/share}/opencode/opencode.db`. It imports sessions
updated within the last 15 minutes by default, plus any session whose directory
matches a currently running `opencode` process. Imported sessions are tagged
with `workspace_hint=opencode-db`, so stale cleanup only ends sessions created
by this DB path and does not close plugin/hook/replay-driven OpenCode sessions.
Use `--active-seconds 0` for a one-time full unarchived DB import, or
`--keep-stale` when you want the daemon to retain old DB sessions. Avoid running
the DB watcher for the same active OpenCode session as the live plugin unless
you specifically need recovery/backfill.

Replay currently running Codex CLI sessions into the daemon as a startup
backfill/recovery path:

```bash
python3 -m codeisland_linux.codex_adapter
```

The Codex adapter can still follow JSONL files for diagnostics:

```bash
python3 -m codeisland_linux.codex_adapter --watch
```

The Codex adapter discovers live sessions from open Codex file descriptors under
`$CODEX_HOME/sessions` (default `~/.codex/sessions`) and maps them into the same
daemon contract used by the DMS plugin.
The initial replay keeps session metadata, the latest user/task anchor, and the
latest 120 JSONL lines per live session by default to keep daemon snapshots
small; pass `--history-lines 0` only when you really want to replay a full
rollout file.
In watch mode the adapter periodically marks Codex daemon sessions ended when
their rollout file is no longer held by a running Codex process, so old hook
smoke-test sessions or closed TUIs do not remain visible forever. When the
Codex hook bridge below is enabled, this watcher still owns lifecycle recovery
and replays still-running sessions after daemon restarts; the hook bridge only
adds lower-latency foreground prompt/tool/permission events.

Replay currently running Claude Code sessions into the daemon as a startup
backfill/recovery path:

```bash
python3 -m codeisland_linux.claude_adapter
```

The Claude Code adapter can still follow JSONL files for diagnostics:

```bash
python3 -m codeisland_linux.claude_adapter --watch
```

The Claude Code adapter discovers running `claude`/`claude-code` processes,
prefers open JSONL file descriptors under `$CLAUDE_HOME/projects` or
`$CLAUDE_HOME/transcripts` (default `~/.claude`), and falls back to the latest
project JSONL for the process cwd. It maps user prompts, assistant text,
tool-use blocks, and tool-result records into the daemon contract with
`provider=claude` and `source=claude-code`. Local command/meta records are
ignored as prompt tasks so `/resume` and command stdout do not become visible
CodeIsland work items. With the Claude hook bridge below enabled, this adapter
is not the primary live path, but watch mode can run as a recovery path and
replay still-running sessions after daemon restarts.

Install an optional global Codex hook bridge for lower-latency events:

```bash
python3 -m codeisland_linux.codex_hook install --global
```

This writes CodeIsland commands into `$CODEX_HOME/hooks.json` when
`CODEX_HOME` is set, otherwise `~/.codex/hooks.json`. The default install keeps
Codex open/close responsive by relying on the JSONL watcher for lifecycle
recovery and installing only foreground prompt/tool/permission hooks.

Alternatively, install into one project only:

```bash
python3 -m codeisland_linux.codex_hook install --project /path/to/project
```

Both modes merge CodeIsland commands into the target `hooks.json` for
`UserPromptSubmit`, `PreToolUse`, `PermissionRequest`, and `PostToolUse`.
Older CodeIsland `SessionStart` and `Stop` commands are removed during install
because Codex waits synchronously for those hooks while opening and closing the
TUI. `PermissionRequest` opens a CodeIsland approval in the daemon, waits for
approve/deny, then prints Codex's expected
`hookSpecificOutput` decision JSON; if the daemon is unavailable or the approval
times out, the hook exits without a decision so Codex can continue its normal
approval path. Existing hooks in that file are preserved. Use `--dry-run` to
preview the merged file and `--backup` to copy the previous `hooks.json` before
writing. Codex hooks must still be enabled and trusted in Codex itself; on
current Codex builds this means
`[features].hooks = true` in the user config plus approving changed hooks in
`/hooks`. Avoid installing CodeIsland hooks both globally and in the same
project, or the same Codex turn may emit duplicate live hook events.

Install the global Claude Code hook bridge:

```bash
python3 -m codeisland_linux.claude_hook install --backup
```

This merges CodeIsland commands into `~/.claude/settings.json` while preserving
existing permissions, model, plugin, and hook settings. The Claude bridge uses
`SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PermissionRequest`,
`PostToolUse`, `PostToolUseFailure`, `Stop`, `StopFailure`, and `SessionEnd`.
Permission requests open daemon approval cards and return Claude's
`hookSpecificOutput` decision only after the DMS interaction is resolved. If the
daemon is unavailable or the approval times out, the hook exits without a
decision so Claude Code can continue its native permission path.

Observe daemon events:

```bash
python3 -m codeisland_linux.subscriber --pretty
```

By default the daemon listens on:

- `$XDG_RUNTIME_DIR/codeislandd.sock` when `XDG_RUNTIME_DIR` is present
- `/tmp/codeisland-<uid>/codeislandd.sock` as a user-scoped fallback

## Test

```bash
python3 -m unittest discover -s tests -v
```
