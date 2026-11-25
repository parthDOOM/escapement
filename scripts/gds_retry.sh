#!/usr/bin/env bash
# The gds job's retry after OpenROAD's placement crash (D-72, docs/24-TOOL-BUGS.md, #11977).
#
#   scripts/gds_retry.sh check   after a failed attempt: retry=true in $GITHUB_OUTPUT only if
#                                global placement crashed (Signal 11 or RSZ-1008); any other
#                                failure is a real one and is not retried
#   scripts/gds_retry.sh clean   before the next attempt, once the crashed attempt's logs are
#                                kept under another name: delete its GDS_logs artifact, which
#                                the action would otherwise fail to upload again, and its runs/
set -euo pipefail

case "${1:-}" in
check)
    log=$(ls runs/wokwi/*-openroad-globalplacement/openroad-globalplacement.log 2>/dev/null | head -1 || true)
    if [ -n "$log" ] && grep -qE 'Signal 11 received|RSZ-1008' "$log"; then
        echo "retry=true" >> "$GITHUB_OUTPUT"
        echo "Global placement crashed (OpenROAD #11977, $log): building again." | tee -a "$GITHUB_STEP_SUMMARY"
    else
        echo "retry=false" >> "$GITHUB_OUTPUT"
        echo "The build failed, and not by the placement crash: no retry." | tee -a "$GITHUB_STEP_SUMMARY"
    fi
    ;;
clean)
    # The artifact list can lag the upload by a few seconds.
    id=""
    for _ in $(seq 12); do
        id=$(gh api "repos/$GITHUB_REPOSITORY/actions/runs/$GITHUB_RUN_ID/artifacts?name=GDS_logs" \
               --jq '.artifacts | sort_by(.created_at) | last | .id // empty')
        [ -n "$id" ] && break
        sleep 5
    done
    if [ -n "$id" ]; then
        gh api -X DELETE "repos/$GITHUB_REPOSITORY/actions/artifacts/$id"
        echo "deleted GDS_logs ($id)"
    else
        echo "no GDS_logs artifact to delete"
    fi
    rm -rf runs tt_submission
    ;;
*)
    echo "usage: $0 check|clean" >&2
    exit 2
    ;;
esac
