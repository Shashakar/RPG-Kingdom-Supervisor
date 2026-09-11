#!/usr/bin/env bash

# Shared, side-effect-free routing policy for RPG Kingdom Symphony workers.
# Input labels are newline-delimited, exactly as returned by GitHub.

rpgk_has_label() {
  local needle="$1"
  local labels="$2"
  grep -Fxiq -- "$needle" <<<"$labels"
}

rpgk_count_labels() {
  local labels="$1"
  shift
  local count=0
  local label
  for label in "$@"; do
    if rpgk_has_label "$label" "$labels"; then
      count=$((count + 1))
    fi
  done
  printf '%s\n' "$count"
}

# Prints: <model>\t<reasoning-effort>\t<route-name>
# Returns non-zero for conflicting explicit routing policy.
rpgk_select_route() {
  local labels="${1:-}"

  local model_labels=(model:luna model:terra model:sol model:astra)
  local risk_labels=(risk:mechanical risk:normal risk:investigative risk:architecture risk:end-to-end)
  local effort_labels=(effort:low effort:medium effort:high)
  local repair_labels=(repair-route:luna repair-route:terra repair-route:sol repair-route:astra)

  local model_count
  model_count="$(rpgk_count_labels "$labels" "${model_labels[@]}")"
  if (( model_count > 1 )); then
    printf 'conflicting model labels\n' >&2
    return 2
  fi

  local risk_count
  risk_count="$(rpgk_count_labels "$labels" "${risk_labels[@]}")"
  if (( risk_count > 1 )); then
    printf 'conflicting risk labels\n' >&2
    return 3
  fi

  local effort_count
  effort_count="$(rpgk_count_labels "$labels" "${effort_labels[@]}")"
  if (( effort_count > 1 )); then
    printf 'conflicting effort labels\n' >&2
    return 4
  fi

  local repair_count
  repair_count="$(rpgk_count_labels "$labels" "${repair_labels[@]}")"
  if (( repair_count > 1 )); then
    printf 'conflicting repair route labels\n' >&2
    return 5
  fi
  if (( repair_count > 0 )) && ! rpgk_has_label symphony:rework "$labels"; then
    printf 'repair route label present outside symphony:rework state\n' >&2
    return 6
  fi

  local model route default_effort

  # Human/operator model overrides retain precedence over reviewer recommendations.
  if rpgk_has_label model:astra "$labels"; then
    model="gpt-6-astra"
    route="astra"
    default_effort="medium"
  elif rpgk_has_label model:sol "$labels"; then
    model="gpt-5.6-sol"
    route="sol"
    default_effort="high"
  elif rpgk_has_label model:luna "$labels"; then
    model="gpt-5.6-luna"
    route="luna"
    default_effort="low"
  elif rpgk_has_label model:terra "$labels"; then
    model="gpt-5.6-terra"
    route="terra"
    default_effort="medium"
  # A structured review recommendation is fresh routing evidence for rework only.
  elif rpgk_has_label symphony:rework "$labels" && rpgk_has_label repair-route:astra "$labels"; then
    model="gpt-6-astra"
    route="astra"
    default_effort="medium"
  elif rpgk_has_label symphony:rework "$labels" && rpgk_has_label repair-route:sol "$labels"; then
    model="gpt-5.6-sol"
    route="sol"
    default_effort="high"
  elif rpgk_has_label symphony:rework "$labels" && rpgk_has_label repair-route:terra "$labels"; then
    model="gpt-5.6-terra"
    route="terra"
    default_effort="medium"
  elif rpgk_has_label symphony:rework "$labels" && rpgk_has_label repair-route:luna "$labels"; then
    model="gpt-5.6-luna"
    route="luna"
    default_effort="low"
  elif rpgk_has_label risk:end-to-end "$labels"; then
    model="gpt-6-astra"
    route="astra"
    default_effort="medium"
  elif rpgk_has_label risk:architecture "$labels"; then
    model="gpt-5.6-sol"
    route="sol"
    default_effort="high"
  elif rpgk_has_label risk:investigative "$labels"; then
    model="gpt-5.6-terra"
    route="terra"
    default_effort="medium"
  elif rpgk_has_label risk:mechanical "$labels"; then
    model="gpt-5.6-luna"
    route="luna"
    default_effort="low"
  else
    model="gpt-5.6-luna"
    route="luna"
    default_effort="medium"
  fi

  local effort="$default_effort"
  if rpgk_has_label effort:low "$labels"; then
    effort="low"
  elif rpgk_has_label effort:medium "$labels"; then
    effort="medium"
  elif rpgk_has_label effort:high "$labels"; then
    effort="high"
  fi

  printf '%s\t%s\t%s\n' "$model" "$effort" "$route"
}
