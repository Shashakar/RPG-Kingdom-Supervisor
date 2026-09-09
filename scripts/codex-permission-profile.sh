#!/usr/bin/env bash

# Shared Codex permission profile for unattended RPG Kingdom workers.
#
# Do not inherit `:workspace` here. Codex's built-in workspace profile adds
# protected read-only metadata rules for .git/.agents/.codex. A Symphony worker
# must be able to perform normal local Git operations, so this profile states
# the workspace policy explicitly and reopens only `.git` for writes.
#
# Everything outside the active workspace remains read-only. `.agents` and
# `.codex` remain protected because they have no narrower write grant. /tmp is
# writable for ordinary tool scratch space. Network is enabled because workers
# need GitHub and package access.

RPGK_CODEX_PERMISSION_PROFILE="rpgk_supervisor_workspace"
RPGK_CODEX_PERMISSION_PROFILE_TOML='{filesystem={":root"="read",":workspace_roots"={"."="write",".git"="write"},":slash_tmp"="write",":tmpdir"="write"},network={enabled=true}}'

RPGK_CODEX_PERMISSION_ARGS=(
  --config "default_permissions=\"$RPGK_CODEX_PERMISSION_PROFILE\""
  --config "permissions.$RPGK_CODEX_PERMISSION_PROFILE=$RPGK_CODEX_PERMISSION_PROFILE_TOML"
)
