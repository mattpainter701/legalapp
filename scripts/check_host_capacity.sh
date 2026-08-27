#!/usr/bin/env bash
# Fail closed when a production host cannot safely run the single-host stack.
set -euo pipefail

# The production override defines 17.5 GiB of memory ceilings across the
# services that have a memory limit. Those ceilings are not reservations, and
# several build/init processes remain uncapped. Keep a meaningful host/daemon
# margin above that configured sum while allowing advertised 32 GB hosts to
# report less than 32 GiB to the guest OS.
readonly STANDARD_MIN_HOST_CPUS=8
readonly STANDARD_MIN_HOST_MEMORY_KIB=$((24 * 1024 * 1024))
readonly CUBE_M_MIN_HOST_CPUS=4
readonly CUBE_M_MIN_HOST_MEMORY_KIB=$((14 * 1024 * 1024))
readonly VPS_MIN_DISK_TOTAL_KIB=$((160 * 1024 * 1024))
readonly VPS_MIN_DISK_AVAILABLE_KIB=$((25 * 1024 * 1024))
readonly HYPERVISOR_MIN_DISK_TOTAL_KIB=$((80 * 1024 * 1024))
readonly HYPERVISOR_MIN_DISK_AVAILABLE_KIB=$((15 * 1024 * 1024))
readonly CUBE_M_MIN_DISK_TOTAL_KIB=$((200 * 1024 * 1024))
readonly CUBE_M_MIN_DISK_AVAILABLE_KIB=$((30 * 1024 * 1024))
# Builds and pre-deploy recovery artifacts consume transient space after this
# check runs. Reserve a fixed amount in addition to the free space needed for
# `df` to remain strictly below the runtime DISK_MAX_PERCENT gate.
readonly DEFAULT_DISK_MAX_PERCENT=85
readonly DEPLOY_BUILD_HEADROOM_KIB=$((5 * 1024 * 1024))

format_gib() {
  awk -v kib="$1" 'BEGIN { printf "%.1f", kib / 1024 / 1024 }'
}

validate_capacity() {
  local profile="$1" cpus="$2" memory_kib="$3" disk_total_kib="$4"
  local disk_used_kib="$5" disk_available_kib="$6" disk_path="$7"
  local disk_max_percent="${DISK_MAX_PERCENT:-$DEFAULT_DISK_MAX_PERCENT}"
  local min_host_cpus min_host_memory_kib
  local min_disk_total_kib min_disk_available_kib disk_usable_kib
  local runtime_free_percent runtime_free_kib threshold_required_kib
  local effective_required_kib
  local failures=()

  case "$profile" in
    vps)
      min_host_cpus="$STANDARD_MIN_HOST_CPUS"
      min_host_memory_kib="$STANDARD_MIN_HOST_MEMORY_KIB"
      min_disk_total_kib="$VPS_MIN_DISK_TOTAL_KIB"
      min_disk_available_kib="$VPS_MIN_DISK_AVAILABLE_KIB"
      ;;
    hypervisor)
      min_host_cpus="$STANDARD_MIN_HOST_CPUS"
      min_host_memory_kib="$STANDARD_MIN_HOST_MEMORY_KIB"
      min_disk_total_kib="$HYPERVISOR_MIN_DISK_TOTAL_KIB"
      min_disk_available_kib="$HYPERVISOR_MIN_DISK_AVAILABLE_KIB"
      ;;
    cube-m)
      min_host_cpus="$CUBE_M_MIN_HOST_CPUS"
      min_host_memory_kib="$CUBE_M_MIN_HOST_MEMORY_KIB"
      min_disk_total_kib="$CUBE_M_MIN_DISK_TOTAL_KIB"
      min_disk_available_kib="$CUBE_M_MIN_DISK_AVAILABLE_KIB"
      ;;
    *)
      echo "ERROR: unknown host capacity profile: $profile" >&2
      return 2
      ;;
  esac

  [[ "$cpus" =~ ^[0-9]+$ ]] || failures+=("online CPU count could not be determined")
  [[ "$memory_kib" =~ ^[0-9]+$ ]] || failures+=("total memory could not be determined")
  [[ "$disk_total_kib" =~ ^[0-9]+$ ]] || failures+=("filesystem size could not be determined for $disk_path")
  [[ "$disk_used_kib" =~ ^[0-9]+$ ]] || failures+=("used disk space could not be determined for $disk_path")
  [[ "$disk_available_kib" =~ ^[0-9]+$ ]] || failures+=("free disk space could not be determined for $disk_path")
  [[ "$disk_max_percent" =~ ^[0-9]+$ ]] \
    && (( disk_max_percent >= 1 && disk_max_percent <= 100 )) \
    || failures+=("DISK_MAX_PERCENT must be an integer from 1 to 100")

  if [[ "$cpus" =~ ^[0-9]+$ ]] && (( cpus < min_host_cpus )); then
    failures+=("$cpus online CPU(s); at least $min_host_cpus are required")
  fi
  if [[ "$memory_kib" =~ ^[0-9]+$ ]] && (( memory_kib < min_host_memory_kib )); then
    failures+=("$(format_gib "$memory_kib") GiB RAM; at least $(format_gib "$min_host_memory_kib") GiB is required")
  fi
  if [[ "$disk_total_kib" =~ ^[0-9]+$ ]] && (( disk_total_kib < min_disk_total_kib )); then
    failures+=("$(format_gib "$disk_total_kib") GiB total on $disk_path; at least $(format_gib "$min_disk_total_kib") GiB is required for the $profile profile")
  fi
  if [[ "$disk_total_kib" =~ ^[0-9]+$ && "$disk_used_kib" =~ ^[0-9]+$ \
        && "$disk_available_kib" =~ ^[0-9]+$ ]]; then
    if (( disk_used_kib > disk_total_kib \
          || disk_available_kib > disk_total_kib \
          || disk_used_kib + disk_available_kib > disk_total_kib )); then
      failures+=("filesystem usage values are inconsistent for $disk_path")
    elif [[ "$disk_max_percent" =~ ^[0-9]+$ ]] \
      && (( disk_max_percent >= 1 && disk_max_percent <= 100 )); then
      # POSIX df rounds capacity upward and production_check rejects capacity
      # equal to the threshold. Keeping the exact used/(used+available) ratio
      # at or below threshold-1 guarantees the post-build df value remains
      # strictly below DISK_MAX_PERCENT.
      disk_usable_kib=$((disk_used_kib + disk_available_kib))
      runtime_free_percent=$((101 - disk_max_percent))
      runtime_free_kib=$(((disk_usable_kib * runtime_free_percent + 99) / 100))
      threshold_required_kib=$((runtime_free_kib + DEPLOY_BUILD_HEADROOM_KIB))
      effective_required_kib="$min_disk_available_kib"
      if (( threshold_required_kib > effective_required_kib )); then
        effective_required_kib="$threshold_required_kib"
      fi
      if (( disk_available_kib < effective_required_kib )); then
        failures+=("$(format_gib "$disk_available_kib") GiB free on $disk_path; at least $(format_gib "$effective_required_kib") GiB is required before deployment for the $profile profile (profile floor $(format_gib "$min_disk_available_kib") GiB; DISK_MAX_PERCENT=$disk_max_percent reserve plus $(format_gib "$DEPLOY_BUILD_HEADROOM_KIB") GiB build headroom)")
      fi
    elif (( disk_available_kib < min_disk_available_kib )); then
      failures+=("$(format_gib "$disk_available_kib") GiB free on $disk_path; at least $(format_gib "$min_disk_available_kib") GiB is required before deployment for the $profile profile")
    fi
  fi

  if (( ${#failures[@]} )); then
    echo "Host capacity check FAILED (${#failures[@]} issue(s)):" >&2
    for failure in "${failures[@]}"; do echo " - $failure" >&2; done
    echo "Refuse this host or pass the documented, process-only HOST_CAPACITY_OVERRIDE with a recorded reason." >&2
    return 1
  fi

  echo "Host capacity passed ($profile): ${cpus} CPU(s), $(format_gib "$memory_kib") GiB RAM, $(format_gib "$disk_total_kib") GiB total / $(format_gib "$disk_available_kib") GiB free on $disk_path (required $(format_gib "$effective_required_kib") GiB with DISK_MAX_PERCENT=$disk_max_percent and $(format_gib "$DEPLOY_BUILD_HEADROOM_KIB") GiB build headroom)."
}

nearest_existing_path() {
  local candidate="$1" parent
  while [[ ! -e "$candidate" ]]; do
    parent="$(dirname -- "$candidate")"
    [[ "$parent" != "$candidate" ]] || break
    candidate="$parent"
  done
  [[ -e "$candidate" ]] || candidate="/"
  printf '%s' "$candidate"
}

# Read the canonical JSON emitted by `docker compose config --format json` and
# return every host bind source.  Parsing the resolved model, rather than the
# checked-in YAML text, covers interpolation and any future bind mount without
# relying on indentation or short-volume-syntax parsing.
extract_compose_bind_sources() {
  command -v python3 >/dev/null 2>&1 || {
    echo "ERROR: python3 is required to inspect resolved Compose bind mounts" >&2
    return 2
  }
  python3 -c '
import json
import posixpath
import sys

try:
    model = json.load(sys.stdin)
except (json.JSONDecodeError, OSError) as exc:
    print(f"ERROR: resolved Compose JSON could not be parsed: {exc}", file=sys.stderr)
    raise SystemExit(2)

services = model.get("services")
if not isinstance(services, dict):
    print("ERROR: resolved Compose JSON has no services object", file=sys.stderr)
    raise SystemExit(2)

for service_name, service in services.items():
    if not isinstance(service, dict):
        print(f"ERROR: resolved Compose service {service_name!r} is invalid", file=sys.stderr)
        raise SystemExit(2)
    volumes = service.get("volumes", [])
    if volumes is None:
        continue
    if not isinstance(volumes, list):
        print(f"ERROR: resolved Compose volumes for {service_name!r} are invalid", file=sys.stderr)
        raise SystemExit(2)
    for volume in volumes:
        if not isinstance(volume, dict):
            print(f"ERROR: resolved Compose volume for {service_name!r} is invalid", file=sys.stderr)
            raise SystemExit(2)
        if volume.get("type") != "bind":
            continue
        source = volume.get("source")
        if (
            not isinstance(source, str)
            or not posixpath.isabs(source)
            or source == "/"
            or "\n" in source
            or "\r" in source
        ):
            print(
                f"ERROR: resolved bind source for {service_name!r} must be an absolute non-root single-line path",
                file=sys.stderr,
            )
            raise SystemExit(2)
        print(source)
'
}

main() {
  if (( $# < 2 )); then
    echo "Usage: bash scripts/check_host_capacity.sh <vps|hypervisor|cube-m> <capacity-path> [capacity-path ...]" >&2
    return 2
  fi

  local profile="$1"
  case "$profile" in
    vps|hypervisor|cube-m) ;;
    *) echo "ERROR: host capacity profile must be vps, hypervisor, or cube-m" >&2; return 2 ;;
  esac

  local override="${HOST_CAPACITY_OVERRIDE:-false}"
  local override_reason="${HOST_CAPACITY_OVERRIDE_REASON:-}"
  case "$override" in
    true|false) ;;
    *) echo "ERROR: HOST_CAPACITY_OVERRIDE must be true or false" >&2; return 2 ;;
  esac
  if [[ "$override" == true ]]; then
    if [[ ! "$override_reason" =~ [^[:space:]] || ${#override_reason} -lt 12 ]]; then
      echo "ERROR: HOST_CAPACITY_OVERRIDE=true requires a specific HOST_CAPACITY_OVERRIDE_REASON of at least 12 characters" >&2
      return 2
    fi
    echo "WARNING: host capacity gate overridden for the $profile profile in this process: $override_reason" >&2
    echo "WARNING: this override is not production-acceptance evidence and must not be persisted in .env." >&2
    return 0
  fi
  [[ -z "$override_reason" ]] || {
    echo "ERROR: HOST_CAPACITY_OVERRIDE_REASON is only valid with HOST_CAPACITY_OVERRIDE=true" >&2
    return 2
  }

  shift

  local requested_path disk_path disk_device cpus memory_kib
  local disk_total_kib disk_used_kib disk_available_kib failed=0
  cpus="$(getconf _NPROCESSORS_ONLN 2>/dev/null || nproc 2>/dev/null || true)"
  memory_kib="$(awk '/^MemTotal:/ {print $2; exit}' /proc/meminfo 2>/dev/null || true)"
  declare -A checked_devices=()
  for requested_path in "$@"; do
    disk_path="$(nearest_existing_path "$requested_path")"
    read -r disk_device disk_total_kib disk_used_kib disk_available_kib < <(
      df -Pk "$disk_path" 2>/dev/null | awk 'NR == 2 { print $1, $2, $3, $4; exit }'
    ) || true
    disk_device="${disk_device:-unresolved:$disk_path}"
    [[ -z "${checked_devices[$disk_device]:-}" ]] || continue
    checked_devices["$disk_device"]=1
    if ! validate_capacity "$profile" "$cpus" "$memory_kib" \
      "${disk_total_kib:-}" "${disk_used_kib:-}" \
      "${disk_available_kib:-}" "$disk_path"; then
      failed=1
    fi
  done
  return "$failed"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
