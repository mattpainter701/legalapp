#!/usr/bin/env bash
# Render the host disk user units. The installer and CI syntax gate share this
# exact path so the verified unit is the unit production installs.
set -euo pipefail

if [[ $# -ne 6 ]]; then
  echo "Usage: $0 UNIT_DIR LEGALAPP_ROOT ENV_FILE COMPOSE_FILES PYTHON_BIN EXEC_PATH" >&2
  exit 2
fi

UNIT_DIR="$1"
LEGALAPP_ROOT="$2"
ENV_FILE="$3"
COMPOSE_FILES="$4"
PYTHON_BIN="$5"
EXEC_PATH="$6"

for value in "$LEGALAPP_ROOT" "$ENV_FILE" "$COMPOSE_FILES" "$PYTHON_BIN" "$EXEC_PATH"; do
  [[ "$value" != *$'\n'* && "$value" != *$'\r'* && "$value" != *'"'* &&
     "$value" != *'@'* && "$value" != *'|'* && "$value" != *'&'* &&
     "$value" != *'%'* && "$value" != *'\\'* && "$value" != *'$'* ]] || {
    echo "ERROR: paths contain characters unsupported by the host disk unit renderer" >&2
    exit 2
  }
done
for path_value in "$LEGALAPP_ROOT" "$ENV_FILE" "$PYTHON_BIN" "$EXEC_PATH"; do
  [[ "$path_value" != *[[:space:]]* ]] || {
    echo "ERROR: executable, root, environment, and PATH values may not contain whitespace" >&2
    exit 2
  }
done
read -r -a compose_file_tokens <<< "$COMPOSE_FILES"
(( ${#compose_file_tokens[@]} > 0 )) \
  && [[ "${compose_file_tokens[*]}" == "$COMPOSE_FILES" ]] || {
    echo "ERROR: COMPOSE_FILES must use one space between validated paths" >&2
    exit 2
  }
[[ ! -L "$UNIT_DIR" ]] || {
  echo "ERROR: host disk unit output directory may not be a symlink" >&2
  exit 2
}
mkdir -p "$UNIT_DIR"
[[ -d "$UNIT_DIR" && ! -L "$UNIT_DIR" ]] || {
  echo "ERROR: host disk unit output must be a non-symlink directory" >&2
  exit 2
}
chmod 700 "$UNIT_DIR"

sed \
  -e "s|@LEGALAPP_ROOT@|$LEGALAPP_ROOT|g" \
  -e "s|@ENV_FILE@|$ENV_FILE|g" \
  -e "s|@COMPOSE_FILES@|$COMPOSE_FILES|g" \
  -e "s|@PYTHON_BIN@|$PYTHON_BIN|g" \
  -e "s|@EXEC_PATH@|$EXEC_PATH|g" \
  "$LEGALAPP_ROOT/ops/systemd/legalapp-host-disk.service.in" \
  > "$UNIT_DIR/legalapp-host-disk.service"
install -m 600 "$LEGALAPP_ROOT/ops/systemd/legalapp-host-disk.timer" \
  "$UNIT_DIR/legalapp-host-disk.timer"
install -m 600 "$LEGALAPP_ROOT/ops/systemd/legalapp-host-disk-failure@.service" \
  "$UNIT_DIR/legalapp-host-disk-failure@.service"
chmod 600 "$UNIT_DIR/legalapp-host-disk.service"
