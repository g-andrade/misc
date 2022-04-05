#!/usr/bin/env bash
set -eu

function log {
    MSG=$1
    echo "$MSG" >&2
}

function fail {
    MSG=$1
    log "[fail] $MSG"
    exit 1
}

function ensure_we_are_on_develop {
    [ "$(git rev-parse --abbrev-ref HEAD)" == "develop" ] || fail "not on develop"
}

function ensure_there_are_no_uncommitted_changes {
    git diff-index --quiet HEAD -- || fail "uncommitted changes"
}

function ensure_latest_info_on_remotes {
    git fetch --all
}

function ensure_master_is_up_to_date {
    git checkout master
    git pull origin master --ff-only
    git checkout develop
}

function ensure_develop_is_ahead_of_origin {
    DIFFERENCES=$(git rev-list --left-right --count origin/develop...develop)
    HOW_MUCH_IS_ORIGIN_AHEAD=$(echo "$DIFFERENCES" | awk '{print $1;}')
    HOW_MUCH_WE_ARE_AHEAD=$(echo "$DIFFERENCES" | awk '{print $2;}')

    [ "$HOW_MUCH_IS_ORIGIN_AHEAD" == "0" ] || fail "origin/develop is ahead of us"
    [ "$HOW_MUCH_WE_ARE_AHEAD" != "0" ] || fail "we are not ahead of origin/develop"
}

function determine_previous_version {
    git tag -l | sort --version-sort | tail -n1
}

function changes_since_prev_version {
    PREV_VERSION=$1
    if [ "$PREV_VERSION" == "" ]; then
        cat CHANGELOG.md
    else
        grep -B9999999 -E "^## \[$PREV_VERSION\] - [0-9]{4}(-[0-9]{2}){2}( )*$" CHANGELOG.md
    fi
}

function ensure_changes_listed_in_changelog {
    if [ ! -f "CHANGELOG.md" ]; then
        fail "cannot determine changes: CHANGELOG.md is missing"
    fi

    PREV_VERSION=$(determine_previous_version)
    NEW_VERSION=$1
    changes_since_prev_version "$PREV_VERSION" \
        | grep -i -E "^## \[((unreleased)|($NEW_VERSION))\]( - [0-9]{4}(-[0-9]{2}){2})?( )*$" \
        || fail "changes for $NEW_VERSION not found in CHANGELOG.md"
}

function ensure_new_version_in_changelog {
    NEW_VERSION=$1
    CURRENT_DATE=$(date +"%Y-%m-%d")

    if ! sed -i.bak -E \
        "s/^## \[((unreleased)|($NEW_VERSION))\]( - [0-9]{4}(-[0-9]{2}){2})?( )*$/## [$NEW_VERSION] - $CURRENT_DATE/i" \
        CHANGELOG.md ;
        then
            fail "Unable to change CHANGELOG.md"
        else
            [ -f 'CHANGELOG.md.bak' ] && rm "CHANGELOG.md.bak"
    fi

    if ! git diff-index --quiet HEAD CHANGELOG.md ; then
        git commit CHANGELOG.md -m "Preparing CHANGELOG for release"
    fi
}

NEW_VERSION=$1

log "Running checks..."
ensure_we_are_on_develop
ensure_there_are_no_uncommitted_changes
ensure_master_is_up_to_date
ensure_develop_is_ahead_of_origin
ensure_changes_listed_in_changelog "$NEW_VERSION"
log "Checks complete ✅"

log "Proceding with release $NEW_VERSION"
git flow release start "$NEW_VERSION"
ensure_new_version_in_changelog "$NEW_VERSION"
GIT_MERGE_AUTOEDIT=no git flow release finish -m "$NEW_VERSION" "$NEW_VERSION"
git push origin develop master
git push origin "$NEW_VERSION"

log "$NEW_VERSION released"
