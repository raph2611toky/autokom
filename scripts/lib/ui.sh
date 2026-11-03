#!/usr/bin/env bash
# AUTOKOM shared terminal UI — source from Bash scripts.
# Works without additional dependencies and degrades gracefully without a TTY.

if [[ "${AUTOKOM_UI_LOADED:-}" == 1 ]]; then
    return 0
fi
AUTOKOM_UI_LOADED=1

if [[ -t 1 && -z "${NO_COLOR:-}" && "${TERM:-}" != dumb ]]; then
    _AK_RESET=$'\033[0m'
    _AK_BOLD=$'\033[1m'
    _AK_DIM=$'\033[2m'
    _AK_CYAN=$'\033[36m'
    _AK_GREEN=$'\033[32m'
    _AK_YELLOW=$'\033[33m'
    _AK_RED=$'\033[31m'
else
    _AK_RESET='' _AK_BOLD='' _AK_DIM=''
    _AK_CYAN='' _AK_GREEN='' _AK_YELLOW='' _AK_RED=''
fi

ak_init() {
    if [[ -z "${AUTOKOM_LOG_FILE:-}" ]]; then
        local stamp
        stamp="$(date -u +%Y%m%dT%H%M%SZ)"
        if (( EUID == 0 )); then
            install -d -m 0700 /var/log/autokom
            AUTOKOM_LOG_FILE="/var/log/autokom/${stamp}-$$.log"
        else
            AUTOKOM_LOG_FILE="${TMPDIR:-/tmp}/autokom-${stamp}-$$.log"
        fi
    else
        mkdir -p -- "$(dirname -- "$AUTOKOM_LOG_FILE")"
    fi
    (umask 077; touch -- "$AUTOKOM_LOG_FILE")
    chmod 600 -- "$AUTOKOM_LOG_FILE" 2>/dev/null || true
    export AUTOKOM_LOG_FILE
}

ak_log() {
    if [[ -n "${AUTOKOM_LOG_FILE:-}" ]]; then
        printf '%s %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*" >> "$AUTOKOM_LOG_FILE"
    fi
}

ak_title() {
    printf '\n%s%sAUTOKOM%s  %sAutomation by IKOM%s\n' "$_AK_BOLD" "$_AK_CYAN" "$_AK_RESET" "$_AK_DIM" "$_AK_RESET"
    printf '%s%s%s\n' "$_AK_DIM" '────────────────────────────────────────────────────────────' "$_AK_RESET"
    ak_log 'BEGIN AUTOKOM'
}

ak_section() {
    printf '\n%s%s%s\n' "$_AK_BOLD" "$*" "$_AK_RESET"
    ak_log "SECTION: $*"
}

ak_ok() {
    printf '  %s✓%s  %s\n' "$_AK_GREEN" "$_AK_RESET" "$*"
    ak_log "OK: $*"
}

ak_info() {
    printf '  %s›%s  %s\n' "$_AK_CYAN" "$_AK_RESET" "$*"
    ak_log "INFO: $*"
}

ak_warn() {
    printf '  %s!%s  %s\n' "$_AK_YELLOW" "$_AK_RESET" "$*" >&2
    ak_log "WARN: $*"
}

ak_err() {
    printf '  %s✗%s  %s\n' "$_AK_RED" "$_AK_RESET" "$*" >&2
    ak_log "ERROR: $*"
}

ak_kv() {
    printf '  %s%-19s%s %s\n' "$_AK_DIM" "$1" "$_AK_RESET" "$2"
    ak_log "$1: $2"
}

# Runs a noisy command quietly, keeps its complete output in the private log.
# On failure, prints the diagnostic log path, never claims success.
ak_run() {
    local title="$1"; shift
    local tmp pid status spinner_index=0
    local -a frames=('⠋' '⠙' '⠹' '⠸' '⠼' '⠴' '⠦' '⠧' '⠇' '⠏')
    tmp="$(mktemp)"
    printf '  %s›%s  %s' "$_AK_CYAN" "$_AK_RESET" "$title"
    "$@" >"$tmp" 2>&1 &
    pid=$!
    if [[ -t 1 ]]; then
        while kill -0 "$pid" 2>/dev/null; do
            printf '\r  %s%s%s  %s' "$_AK_CYAN" "${frames[spinner_index % ${#frames[@]}]}" "$_AK_RESET" "$title"
            spinner_index=$((spinner_index + 1))
            sleep 0.12
        done
    fi
    status=0
    wait "$pid" || status=$?
    if [[ -n "${AUTOKOM_LOG_FILE:-}" ]]; then
        {
            printf '\n--- %s (exit=%s) ---\n' "$title" "$status"
            cat "$tmp"
        } >> "$AUTOKOM_LOG_FILE"
    fi
    if [[ -t 1 ]]; then printf '\r\033[2K'; else printf '\n'; fi
    if (( status == 0 )); then
        ak_ok "$title"
    else
        ak_err "$title (code $status)"
        ak_warn "Diagnostic : ${AUTOKOM_LOG_FILE:-$tmp}"
    fi
    if [[ "${AUTOKOM_VERBOSE:-0}" == 1 ]]; then
        sed 's/^/      /' "$tmp"
    fi
    rm -f -- "$tmp"
    return "$status"
}

ak_finish() {
    printf '\n%s%s%s\n' "$_AK_DIM" '────────────────────────────────────────────────────────────' "$_AK_RESET"
    ak_kv 'Journal' "${AUTOKOM_LOG_FILE:-non défini}"
    ak_log 'END AUTOKOM'
}
