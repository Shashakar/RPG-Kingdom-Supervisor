#!/usr/bin/env bash

# Shared Codex permission profile for unattended RPG Kingdom workers.
#
# We intentionally use the named permission-profile model rather than the
# legacy workspace-write sandbox. The built-in :workspace profile keeps the
# worker scoped to its checkout; the one explicit relaxation is `.git` write
# access so normal fetch/switch/add/commit/push workflows can function.
# Network remains enabled because workers need GitHub and package access.

RPGK_CODEX_PERMISSION_PROFILE="rpgk_supervisor_workspace"
RPGK_CODEX_PERMISSION_PROFILE_TOML='{extends=":workspace",filesystem={":workspace_roots"={".git"="write"}},network={enabled=true}}'

RPGK_CODEX_PERMISSION_ARGS=(
  --config "default_permissions=\"$RPGK_CODEX_PERMISSION_PROFILE\""
  --config "permissions.$RPGK_CODEX_PERMISSION_PROFILE=$RPGK_CODEX_PERMISSION_PROFILE_TOML"
)
