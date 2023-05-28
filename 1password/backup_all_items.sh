#!/usr/bin/env bash

set -eu

## Backs up all 1password items (for the default account?)
## into a GPG-encrypted file (user is prompted for the file password)

GPG_CIPHER="AES256"
DEFAULT_OUTPUT_FILENAME=$(date +"%Y%m%d_%H%M%S_%N_%Z.txt.gpg")

function log {
    MSG=$1
    TIMESTAMP=$(date "+%Y/%m/%d %H:%M:%S")
    echo "[$TIMESTAMP] $MSG" >&2
}

function error {
    MSG=$1
    log "error: $MSG"
    exit 1
}

function check_cmd {
    CMD=$1
    which "$CMD" >/dev/null || error "$CMD is missing"
}

output_path=${1:-$DEFAULT_OUTPUT_FILENAME}
output_path=$(realpath "$output_path")
log "Output path: \"${output_path}\""

check_cmd op
check_cmd gpg

op item list \
| tail -n+2 \
| while read -r item_line; do
    item_id=$(echo "$item_line" | awk '{print $1}')
    item_name=$(echo "$item_line" | awk '{print $2}')
    log "Backing up item $item_id ($item_name)"

    op item get "$item_id"
    echo "------------------------------------"
done \
| gpg -c --no-symkey-cache --cipher "$GPG_CIPHER" >"$output_path"

log "1password items saved in \"$output_path\""
