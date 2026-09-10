#!/usr/bin/env bash

# Shared Codex permission profile for unattended RPG Kingdom workers.
#
# Workers need ordinary source-file writes inside the active GH issue workspace,
# read access to the host, temporary scratch space, and network for research or
# package access. They do not need to own `.git` metadata: branch preparation,
# commit, push, and PR handoff are provided by the bounded host Git broker.
#
# Do not inherit `:workspace` here because Symphony must select this named
# profile deterministically. Codex's protected metadata rules keep .git/.agents/
# .codex outside the worker's ordinary source-write authority.

RPGK_CODEX_PERMISSION_PROFILE="rpgk_supervisor_workspace"
RPGK_CODEX_PERMISSION_PROFILE_TOML='{filesystem={":root"="read",":workspace_roots"={"."="write"},":slash_tmp"="write",":tmpdir"="write"},network={enabled=true}}'

RPGK_CODEX_PERMISSION_ARGS=(
  --config "default_permissions=\"$RPGK_CODEX_PERMISSION_PROFILE\""
  --config "permissions.$RPGK_CODEX_PERMISSION_PROFILE=$RPGK_CODEX_PERMISSION_PROFILE_TOML"
)
