#!/usr/bin/env bash

set -eu

function log {
    MSG=$1
    TIMESTAMP=$(date "+%Y/%m/%d %H:%M:%S")
    echo "[$TIMESTAMP] $MSG"
}

function fail {
    MSG=$1
    log "FAIL: $MSG"
    exit 1
}


flac -v >/dev/null 2>&1 || fail "'flac' not installed"
# wavpack --version >/dev/null 2>&1 || fail "'wavpack' not installed"
parallel --version >/dev/null 2>&1 || fail "'parallel' not installed"


INPUT_DIR=$(realpath $1) || fail 'Missing first argument (input directory)'
OUTPUT_DIR=$(realpath $2) || fail 'Missing second argument (output directory)'

log "Input under \"$INPUT_DIR\", output under \"$OUTPUT_DIR\""
[ "$INPUT_DIR" != "$OUTPUT_DIR" ] || fail 'Input dir cannot be the same as output dir'

mkdir -p "$OUTPUT_DIR"
mkdir -p "$OUTPUT_DIR/unconverted"

pushd "$INPUT_DIR"
set -x

ls *.wav | parallel flac -8 -o "$OUTPUT_DIR/{.}.flac" --preserve-modtime --verify "{}";
# ls *.wav | parallel wavpack -hh -x -t -v "{}" -o "$OUTPUT_DIR/{.}.vw"

for FILE in *.lof; do
    sed 's/\.wav" /.flac" /g' <"$FILE" >"$OUTPUT_DIR/$FILE";
#    sed 's/\.wav" /.vw" /g' <"$FILE" >"$OUTPUT_DIR/$FILE";
    touch -r "$FILE" "$OUTPUT_DIR/$FILE"
done

for FILE in *.rpp; do
    cp -p "$FILE" "$OUTPUT_DIR/unconverted/$FILE"
done

set +x
popd
