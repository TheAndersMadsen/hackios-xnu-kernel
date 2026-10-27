#!/usr/bin/env bash
# run-attempt.sh — ONE command for ONE reproducible laguna USB-fix boot attempt.
#
# Replaces the hand-orchestrated loop that produced the 2026-10-26/27 attempts:
#   preconditions -> arm force-fastboot -> AVB-verify candidate -> start usb_log
#   -> fastboot boot -> bounded watch -> immediate ramdump pull on park
#   -> decode stamps + full reader -> one-line verdict in ATTEMPTS.md.
#
# Device-write contract (the ONLY device writes this script ever performs):
#   1. `fastboot oem force-fastboot on`   (persistent devinfo env; NEVER set off)
#   2. `fastboot boot <candidate.img>`    (temporary boot; no partition write)
#   3. named stage_file/get_staged reads  (kernel.log / abl.log / ... via the
#      proven bounded collector capture-fastboot.py; never a raw ap_* bank,
#      never a bare listing, never a reboot)
#   Everything else (getvar, devices, adb pstore cat) is read-only.
#   This script never flashes, erases, formats, changes slots, sets active,
#   reboots the device, or disarms force-fastboot. Disarming is the
#   coordinator's explicit decision at session end.
#
# Non-interactive: aborts loudly instead of waiting if the phone is not in a
# responsive blazer fastboot. All child processes are hard-bounded.
#
# Usage:
#   research/laguna-usb-fix/run-attempt.sh --image IMG --name NAME [options]
#
# Options:
#   -i, --image IMG        candidate boot image to `fastboot boot` (required)
#   -n, --name NAME        short attempt label, [A-Za-z0-9._-]+ (required)
#       --watch SEC        total watch deadline after boot (default 300)
#       --poll SEC         watch poll interval (default 10)
#       --settle SEC       quiet settle after `fastboot boot` before polling
#                          (default 20; covers the old fastboot session dying)
#       --ep0-grace SEC    extra seconds to let usb_log read after EP0-live
#                          before stopping it (default 15)
#       --mach PATH        Mach-O for read-ramdump symbolication
#                          (default research/laguna-panic-capture/mach.laguna141-paniccap)
#       --dump-base ADDR   ramdump base PA (default 0x95200000)
#       --reader-tail N    read-ramdump --tail (default 65536)
#       --keep-alive       skip the `oem force-fastboot on` arm step (device
#                          must ALREADY be armed; still verified via getvar).
#                          force-fastboot is never disarmed in any mode.
#       --skip-avb         skip the host-only avbtool verify_image gate
#       --dry-run          print the full command plan; run host-only checks
#                          (sha256 + AVB verify) but touch NO device
#       --selftest         host-only checks of the internal helpers, then exit
#   -h, --help             this help
#
# Environment overrides: FASTBOOT, ADB, TEGU_PYTHON (default
# /tmp/tegu-venv/bin/python), PY3 (default python3).

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO=$(cd "$SCRIPT_DIR/../.." && pwd)

IMG=""; NAME=""
WATCH=300; POLL=10; SETTLE=20; EP0_GRACE=15
DUMP_BASE=0x95200000
READER_TAIL=65536
MACH="$REPO/research/laguna-panic-capture/mach.laguna141-paniccap"
KEEP_ALIVE=0; SKIP_AVB=0; DRY_RUN=0; SELFTEST=0

FASTBOOT=${FASTBOOT:-fastboot}
ADB=${ADB:-adb}
VENV_PY=${TEGU_PYTHON:-/tmp/tegu-venv/bin/python}
PY3=${PY3:-python3}

USB_LOG="$REPO/research/tegu-ios/tools/tegu/usb_log.py"
DECODE="$REPO/research/laguna-usb-console/tools/decode-shim-stamps.py"
READER="$REPO/research/laguna-ramdump/reader/read-ramdump.py"
CAPTURE="$REPO/research/laguna-ramdump/tools/capture-fastboot.py"
AVBTOOL="$REPO/research/native-20261024/reuse/felix-working-source/baremetal_loader/avbtool.py"
ATTEMPTS_BASE="$REPO/research/laguna-usb-fix/attempts"
ATTEMPTS_MD="$REPO/research/laguna-usb-fix/ATTEMPTS.md"

EP0_MARKER='Tegu USB diagnostic connected.'
USB_TIMEOUT_MARKER='No tegu diagnostic enumerated before timeout.'
PSTORE_FILE='/sys/fs/pstore/console-ramoops-0'

usage() { sed -n '2,60p' "$0" | sed 's/^# \{0,1\}//'; }

log()  { printf '[run-attempt] %s\n' "$*"; }
warn() { printf '[run-attempt] WARN: %s\n' "$*" >&2; }
die()  { printf '[run-attempt] ABORT: %s\n' "$*" >&2; exit 2; }

report_cmd() { printf '[cmd] '; printf '%q ' "$@"; printf '\n'; }

# --------------------------------------------------------------------------
# run_bounded SECS CMD...  — hard-bounded child process.
# Never fails the caller (set -e safe). Outputs:
#   BOUNDED_OUT      path of the captured stdout+stderr file
#   BOUNDED_RC       child exit status (143/137 on our TERM/KILL)
#   BOUNDED_TIMEDOUT 1 if we had to kill it at the deadline
# --------------------------------------------------------------------------
RUN_TMP=$(mktemp -d "${TMPDIR:-/tmp}/run-attempt.XXXXXX")
AVB_TMP=""
trap '[[ -n "$AVB_TMP" ]] && rm -rf "$AVB_TMP"; rm -rf "$RUN_TMP"' EXIT
BOUNDED_OUT="$RUN_TMP/last.out"; BOUNDED_RC=0; BOUNDED_TIMEDOUT=0

run_bounded() {
    local secs=$1; shift
    BOUNDED_OUT="$RUN_TMP/bounded.$RANDOM.$RANDOM"
    BOUNDED_RC=0; BOUNDED_TIMEDOUT=0
    "$@" >"$BOUNDED_OUT" 2>&1 </dev/null &
    local pid=$!
    ( sleep "$secs"; kill -TERM "$pid" 2>/dev/null; sleep 2
      kill -KILL "$pid" 2>/dev/null ) </dev/null >/dev/null 2>&1 &
    local wd=$!
    if wait "$pid"; then BOUNDED_RC=0; else BOUNDED_RC=$?; fi
    if [[ $BOUNDED_RC -ge 128 && $BOUNDED_RC -le 150 ]]; then BOUNDED_TIMEDOUT=1; fi
    kill -KILL "$wd" 2>/dev/null || true
    wait "$wd" 2>/dev/null || true
    return 0
}

# fb SECS ARGS... — bounded fastboot call; prints the exact command first.
fb() {
    local secs=$1; shift
    report_cmd "$FASTBOOT" "$@"
    run_bounded "$secs" "$FASTBOOT" "$@"
}

stop_logger() {
    [[ -n "${LOG_PID:-}" ]] || return 0
    kill -TERM "$LOG_PID" 2>/dev/null || true
    for _ in 1 2 3 4 5; do
        kill -0 "$LOG_PID" 2>/dev/null || break
        sleep 1
    done
    kill -KILL "$LOG_PID" 2>/dev/null || true
    wait "$LOG_PID" 2>/dev/null || true
    LOG_PID=""
}

# grep_file RE FILE — set -e safe grep.
grep_file() { [[ -s $2 ]] && grep -Eq "$1" "$2" 2>/dev/null; }

# ----------------------------- selftest -----------------------------------
selftest() {
    local fail=0
    log "selftest: run_bounded fast path"
    run_bounded 5 /bin/echo hello
    if grep -q hello "$BOUNDED_OUT" && [[ $BOUNDED_RC -eq 0 && $BOUNDED_TIMEDOUT -eq 0 ]]; then
        log "  PASS"; else log "  FAIL"; fail=1; fi
    log "selftest: run_bounded timeout path (sleep 30 killed at ~2 s)"
    local t0=$SECONDS
    run_bounded 2 /bin/sleep 30
    if [[ $BOUNDED_TIMEDOUT -eq 1 && $((SECONDS - t0)) -le 6 ]]; then
        log "  PASS (rc=$BOUNDED_RC, ${SECONDS}-${t0}s)"; else log "  FAIL"; fail=1; fi
    log "selftest: verdict-line parsers against a decoder-shaped fixture"
    local fixture='  nonce=0x6dbf3154abd454ef count=8/8 seq=8 dropped=16 reason=usb-fatal
  last stage: usb-preflight [FAULT]'
    local reason seq last_stage
    reason=$(printf '%s\n' "$fixture" | sed -n 's/.* reason=\([^ ]*\).*/\1/p' | head -1)
    seq=$(printf '%s\n' "$fixture" | sed -n 's/.* seq=\([0-9]*\).*/\1/p' | head -1)
    last_stage=$(printf '%s\n' "$fixture" | sed -n 's/^ *last stage: /last:/p' | head -1)
    if [[ $reason == usb-fatal && $seq == 8 && $last_stage == 'last:usb-preflight [FAULT]' ]]; then
        log "  PASS"; else log "  FAIL ($reason/$seq/$last_stage)"; fail=1; fi
    if [[ $fail -eq 0 ]]; then log "selftest PASS"; else log "selftest FAIL"; exit 1; fi
}

# ----------------------------- arg parse ----------------------------------
while [[ $# -gt 0 ]]; do
    case $1 in
        -i|--image)      IMG=${2:?}; shift 2;;
        -n|--name)       NAME=${2:?}; shift 2;;
        --watch)         WATCH=${2:?}; shift 2;;
        --poll)          POLL=${2:?}; shift 2;;
        --settle)        SETTLE=${2:?}; shift 2;;
        --ep0-grace)     EP0_GRACE=${2:?}; shift 2;;
        --mach)          MACH=${2:?}; shift 2;;
        --dump-base)     DUMP_BASE=${2:?}; shift 2;;
        --reader-tail)   READER_TAIL=${2:?}; shift 2;;
        --keep-alive)    KEEP_ALIVE=1; shift;;
        --skip-avb)      SKIP_AVB=1; shift;;
        --dry-run)       DRY_RUN=1; shift;;
        --selftest)      SELFTEST=1; shift;;
        -h|--help)       usage; exit 0;;
        *) die "unknown argument: $1 (see --help)";;
    esac
done

[[ $SELFTEST -eq 1 ]] && { selftest; exit 0; }

[[ -n $IMG ]]  || { usage; echo; die "--image is required"; }
[[ -n $NAME ]] || { usage; echo; die "--name is required"; }
[[ $NAME =~ ^[A-Za-z0-9._-]+$ ]] || die "NAME must match [A-Za-z0-9._-]+ (got: $NAME)"
case $WATCH$POLL$SETTLE$EP0_GRACE in *[!0-9]*) die "timeouts must be integers";; esac

# ----------------------------- host checks --------------------------------
[[ -f $IMG ]] || die "candidate image not found: $IMG"
IMG=$(cd "$(dirname "$IMG")" && pwd)/$(basename "$IMG")
for f in "$USB_LOG" "$DECODE" "$READER" "$CAPTURE" "$AVBTOOL" "$MACH"; do
    [[ -f $f ]] || die "required tool/input missing: $f"
done
[[ -x $VENV_PY ]] || die "tegu venv python missing: $VENV_PY (usb_log needs pyusb)"
command -v "$PY3" >/dev/null 2>&1 || die "python3 not found"
command -v "$FASTBOOT" >/dev/null 2>&1 || die "fastboot not in PATH (set FASTBOOT=...)"
if ! command -v "$ADB" >/dev/null 2>&1; then ADB=""; fi   # adb optional

log "candidate: $IMG"
report_cmd shasum -a 256 "$IMG"
IMG_SHA=$(shasum -a 256 "$IMG" | awk '{print $1}')
log "sha256: $IMG_SHA"

# ----------------------------- dry-run plan -------------------------------
if [[ $DRY_RUN -eq 1 ]]; then
    cat <<PLAN
[run-attempt] DRY RUN — no device was touched. Plan for attempt '$NAME':
  1. [host] shasum -a 256 $IMG                     -> $IMG_SHA
  2. [host] (mktemp -d; ln -s IMG boot.img) && $PY3 $AVBTOOL verify_image --image boot.img  $( [[ $SKIP_AVB -eq 1 ]] && echo '[SKIPPED: --skip-avb]' )
  3. [dev ] $FASTBOOT getvar product                          (expect 'product: blazer'; abort if not in fastboot)
  4. [dev ] $FASTBOOT oem force-fastboot on                   (expect OKAY)$( [[ $KEEP_ALIVE -eq 1 ]] && echo '  [SKIPPED: --keep-alive; getvar check only]' )
  5. [dev ] $FASTBOOT getvar force-fastboot                   (expect oem query to echo 'force-fastboot on'; abort otherwise)
  6. [host] $VENV_PY $USB_LOG --timeout $WATCH > attempts/<ts>-$NAME/live.log   (background; single EP0 client)
  7. [dev ] $FASTBOOT boot $IMG
  8. [host] settle ${SETTLE}s, then poll every ${POLL}s until ${WATCH}s deadline:
            live.log size + EP0 marker / fastboot devices+getvar product / adb devices
  9. [dev ] on park:    $PY3 $CAPTURE attempts/<ts>-$NAME/ramdump --diagnostics
            (stage_file+get_staged RAMDUMP_MSG.txt, kernel.log, abl.log, abl_last.log; no reboot)
 10. [host] on dump:    $PY3 $DECODE .../kernel.log --dump-base $DUMP_BASE
 11. [host]             $PY3 $READER .../kernel.log --mach $MACH --dump-base $DUMP_BASE --logs --tail $READER_TAIL
 12. [dev ] on EP0-live/Android: save live.log; if adb: $ADB exec-out su -c 'cat $PSTORE_FILE' (best-effort)
 13. [host] append ONE verdict line to $ATTEMPTS_MD
PLAN
    if [[ $SKIP_AVB -eq 0 ]]; then
        log "dry-run host gate: AVB verify (host-only, no device)"
        AVB_TMP=$(mktemp -d "${TMPDIR:-/tmp}/run-attempt-avb.XXXXXX")
        ln -s "$IMG" "$AVB_TMP/boot.img"
        report_cmd bash -c "cd $AVB_TMP && $PY3 $AVBTOOL verify_image --image boot.img"
        if ( cd "$AVB_TMP" && "$PY3" "$AVBTOOL" verify_image --image boot.img ); then
            log "AVB verify_image: PASS"
        else
            die "AVB verify_image FAILED for $IMG"
        fi
    fi
    log "DRY RUN complete (plan printed; no device commands issued)."
    exit 0
fi

# ----------------------------- attempt dir --------------------------------
TS=$(date +%Y%m%d-%H%M%S)
ATTEMPT_DIR="$ATTEMPTS_BASE/$TS-$NAME"
if [[ -e $ATTEMPT_DIR ]]; then
    die "attempt dir already exists: $ATTEMPT_DIR (rerun in a second or rename)"
fi
mkdir -p "$ATTEMPT_DIR"
LIVE_LOG="$ATTEMPT_DIR/live.log"
log "attempt dir: $ATTEMPT_DIR"

{
    echo "attempt: $NAME"
    echo "started: $(date '+%Y-%m-%d %H:%M:%S %z')"
    echo "image: $IMG"
    echo "image_sha256: $IMG_SHA"
    echo "watch=${WATCH}s poll=${POLL}s settle=${SETTLE}s ep0-grace=${EP0_GRACE}s"
    echo "keep-alive=$KEEP_ALIVE skip-avb=$SKIP_AVB"
    echo "mach: $MACH"
    echo "dump-base: $DUMP_BASE"
    echo "host: $(hostname)"
    echo "device-write contract: ONLY 'fastboot oem force-fastboot on',"
    echo "  'fastboot boot <img>', and named stage_file/get_staged reads."
    echo "  No flash/erase/format/set-active/slot/reboot; force-fastboot never disarmed."
} > "$ATTEMPT_DIR/meta.txt"

# ----------------------------- device preconditions ------------------------
log "precondition: phone must ALREADY be in a responsive blazer fastboot (never waiting)"
fb 5 getvar product
cp "$BOUNDED_OUT" "$ATTEMPT_DIR/preflight-product.out"
if ! grep -Eq 'product:[[:space:]]*blazer([[:space:]]|$)' "$ATTEMPT_DIR/preflight-product.out"; then
    cat "$ATTEMPT_DIR/preflight-product.out" >&2 || true
    die "no responsive blazer fastboot device — put the phone in fastboot first and rerun (aborting, not waiting)"
fi
log "precondition OK: blazer fastboot responsive"

# ----------------------------- AVB gate (host-only) ------------------------
if [[ $SKIP_AVB -eq 0 ]]; then
    AVB_TMP=$(mktemp -d "${TMPDIR:-/tmp}/run-attempt-avb.XXXXXX")
    ln -s "$IMG" "$AVB_TMP/boot.img"
    log "AVB gate: felix avbtool verify_image (boot.img symlink in temp dir)"
    report_cmd bash -c "cd $AVB_TMP && $PY3 $AVBTOOL verify_image --image boot.img"
    if ( cd "$AVB_TMP" && "$PY3" "$AVBTOOL" verify_image --image boot.img ) \
         > "$ATTEMPT_DIR/avb-verify.out" 2>&1; then
        log "AVB verify_image: PASS"
    else
        cat "$ATTEMPT_DIR/avb-verify.out" >&2 || true
        die "AVB verify_image FAILED — refusing to boot an unverifiable candidate"
    fi
else
    warn "AVB verify skipped (--skip-avb)"
fi

# ----------------------------- arm force-fastboot --------------------------
if [[ $KEEP_ALIVE -eq 1 ]]; then
    log "keep-alive: skipping arm step; verifying force-fastboot is already on"
else
    log "arming force-fastboot (persistent devinfo env) so any shim failure reset parks in fastboot"
    fb 15 oem force-fastboot on
    cp "$BOUNDED_OUT" "$ATTEMPT_DIR/ff-arm.out"
    grep -q OKAY "$ATTEMPT_DIR/ff-arm.out" 2>/dev/null \
        || { cat "$ATTEMPT_DIR/ff-arm.out" >&2 || true; die "force-fastboot arm did not return OKAY"; }
fi
fb 10 oem force-fastboot
cp "$BOUNDED_OUT" "$ATTEMPT_DIR/ff-getvar.out"
grep -q 'force-fastboot[[:space:]]*on' "$ATTEMPT_DIR/ff-getvar.out" 2>/dev/null \
    || { cat "$ATTEMPT_DIR/ff-getvar.out" >&2 || true; die "force-fastboot is NOT on — refusing to boot without the autonomous park"; }
log "force-fastboot: on (confirmed; will NOT be disarmed)"

# ----------------------------- start EP0 logger ----------------------------
# usb_log.py --output is unusable outside the tegu nested checkout (path
# allowlist), so redirect stdout/stderr into the attempt dir instead.
log "starting usb_log (single EP0 client) for the full ${WATCH}s watch window"
report_cmd "$VENV_PY" "$USB_LOG" --timeout "$WATCH" ">" "$LIVE_LOG" "2>&1 (background)"
"$VENV_PY" "$USB_LOG" --timeout "$WATCH" >"$LIVE_LOG" 2>&1 </dev/null &
LOG_PID=$!

# ----------------------------- boot ----------------------------------------
log "temporary boot (no partition write)"
report_cmd "$FASTBOOT" boot "$IMG"
run_bounded 90 "$FASTBOOT" boot "$IMG"
cp "$BOUNDED_OUT" "$ATTEMPT_DIR/boot.log" 2>/dev/null || true
sed 's/^/  [boot] /' "$ATTEMPT_DIR/boot.log" 2>/dev/null || true

BOOT_RC=$BOUNDED_RC
if [[ $BOOT_RC -ne 0 || $BOUNDED_TIMEDOUT -eq 1 ]]; then
    warn "fastboot boot failed/stalled (rc=$BOOT_RC timedout=$BOUNDED_TIMEDOUT) — recording and aborting this attempt"
    stop_logger
    OUTCOME=boot-fail
else
    # ----------------------------- watch loop ------------------------------
    log "watching ${WATCH}s (settle ${SETTLE}s first): EP0-live | park | Android | deadline"
    log "reminder: if the panel shows the shim's red HANG stage, photograph it NOW — it disappears on reset"
    sleep "$SETTLE"
    deadline=$((SECONDS + WATCH))
    OUTCOME=deadline
    while :; do
        if grep_file "$EP0_MARKER" "$LIVE_LOG"; then
            OUTCOME=ep0-live; break
        fi
        fb_state=absent
        fb 5 devices
        if [[ -s $BOUNDED_OUT ]]; then
            fb_state=present
            fb 8 getvar product
            if grep -Eq 'product:[[:space:]]*blazer([[:space:]]|$)' "$BOUNDED_OUT" 2>/dev/null; then
                OUTCOME=park; break
            fi
            fb_state=present-unresponsive
        fi
        adb_state=absent
        if [[ -n $ADB ]]; then
            report_cmd "$ADB" devices
            run_bounded 5 "$ADB" devices
            if grep -Eq "$(printf '\t')device$" "$BOUNDED_OUT" 2>/dev/null; then
                adb_state=present
            fi
        fi
        live_size=$(wc -c < "$LIVE_LOG" 2>/dev/null || true); [[ -n $live_size ]] || live_size=0
        log "watch t+$((SECONDS - deadline + WATCH))s/${WATCH}s live=${live_size}B fastboot=$fb_state adb=$adb_state"
        if [[ $adb_state == present ]]; then OUTCOME=android; break; fi
        if (( SECONDS >= deadline )); then OUTCOME=deadline; break; fi
        sleep "$POLL"
    done
fi

# ----------------------------- evidence per outcome ------------------------
DUMP_COL="-"
KERNEL=""
case $OUTCOME in
    park)
        stop_logger
        log "PARKED in fastboot — pulling ramdump IMMEDIATELY (no reboot in between)"
        RD_DIR="$ATTEMPT_DIR/ramdump"   # capture-fastboot.py requires a NEW dir
        report_cmd "$PY3" "$CAPTURE" "$RD_DIR" --diagnostics
        run_bounded 240 "$PY3" "$CAPTURE" "$RD_DIR" --diagnostics
        mv "$BOUNDED_OUT" "$ATTEMPT_DIR/capture.log"
        sed 's/^/  [capture] /' "$ATTEMPT_DIR/capture.log" || true
        KERNEL="$RD_DIR/kernel.log"
        [[ -s $KERNEL ]] || { warn "capture produced no usable kernel.log (see capture.log)"; KERNEL=""; }
        ;;
    ep0-live|android)
        stop_logger
        log "outcome $OUTCOME — live log saved at $LIVE_LOG"
        if [[ $OUTCOME == ep0-live ]]; then
            log "EP0 live: letting usb_log read for ${EP0_GRACE}s grace, then stopping it"
            sleep "$EP0_GRACE"
            stop_logger
        fi
        if [[ -n $ADB ]]; then
            report_cmd "$ADB" exec-out su -c "cat $PSTORE_FILE" ">" "$ATTEMPT_DIR/pstore-console-ramoops-0.txt (best-effort)"
            run_bounded 20 "$ADB" exec-out su -c "cat $PSTORE_FILE" \
                > "$ATTEMPT_DIR/pstore-console-ramoops-0.txt" 2>&1 \
                || warn "pstore pull failed (best-effort, non-fatal)"
            [[ -s $ATTEMPT_DIR/pstore-console-ramoops-0.txt ]] \
                && log "pstore console-ramoops-0 saved ($(wc -c < "$ATTEMPT_DIR/pstore-console-ramoops-0.txt") bytes)" \
                || warn "pstore empty/unavailable (non-fatal)"
        fi
        ;;
    boot-fail|deadline)
        stop_logger
        if [[ $OUTCOME == deadline ]]; then
            warn "watch deadline hit with no EP0 and no park (usb-dead/dark class) — recording state probes"
            fb 5 devices
            cp "$BOUNDED_OUT" "$ATTEMPT_DIR/state-deadline-fastboot.txt" 2>/dev/null || true
            if [[ -n $ADB ]]; then
                run_bounded 5 "$ADB" devices
                cp "$BOUNDED_OUT" "$ATTEMPT_DIR/state-deadline-adb.txt" 2>/dev/null || true
            fi
        fi
        ;;
esac

# ----------------------------- decode + read -------------------------------
LAST_STAGE="none"; REASON="none"; SEQ="-"
if [[ -n $KERNEL && -s $KERNEL ]]; then
    DUMP_COL="attempts/$(basename "$ATTEMPT_DIR")/ramdump/kernel.log"
    report_cmd "$PY3" "$DECODE" "$KERNEL" --dump-base "$DUMP_BASE"
    run_bounded 60 "$PY3" "$DECODE" "$KERNEL" --dump-base "$DUMP_BASE"
    mv "$BOUNDED_OUT" "$ATTEMPT_DIR/stamps-decode.txt"
    sed 's/^/  [stamps] /' "$ATTEMPT_DIR/stamps-decode.txt" || true

    report_cmd "$PY3" "$READER" "$KERNEL" --mach "$MACH" --dump-base "$DUMP_BASE" --logs --tail "$READER_TAIL"
    run_bounded 180 "$PY3" "$READER" "$KERNEL" --mach "$MACH" \
        --dump-base "$DUMP_BASE" --logs --tail "$READER_TAIL"
    mv "$BOUNDED_OUT" "$ATTEMPT_DIR/reader.txt"
    log "full reader output saved: $ATTEMPT_DIR/reader.txt"
    head -n 12 "$ATTEMPT_DIR/reader.txt" | sed 's/^/  [reader] /' || true

    nonce_line=$(sed -n '2p' "$ATTEMPT_DIR/stamps-decode.txt" 2>/dev/null || true)
    REASON=$(printf '%s\n' "$nonce_line" | sed -n 's/.* reason=\([^ ]*\).*/\1/p' | head -1)
    SEQ=$(printf '%s\n' "$nonce_line" | sed -n 's/.* seq=\([0-9]*\).*/\1/p' | head -1)
    LAST_STAGE=$(sed -n 's/^ *last stage: //p' "$ATTEMPT_DIR/stamps-decode.txt" | head -1)
    [[ -n $REASON ]] || REASON=decode-no-reason
    [[ -n $SEQ ]] || SEQ=-
    [[ -n $LAST_STAGE ]] || LAST_STAGE=decode-no-stage
fi

# ----------------------------- verdict -------------------------------------
EP0Q=no
if grep -q "$EP0_MARKER" "$LIVE_LOG" 2>/dev/null; then EP0Q=yes; fi

case $OUTCOME in
    boot-fail) LAST_STAGE="boot-send";      REASON="boot-failed-or-stalled"; SEQ="-";;
    park)      [[ -z $KERNEL ]] && { LAST_STAGE="no-dump"; REASON="capture-failed"; SEQ="-"; } ;;
    ep0-live)  [[ $LAST_STAGE == none ]] && { LAST_STAGE="ep0-live"; REASON="usb-console-live"; SEQ="-"; } ;;
    android)   [[ $LAST_STAGE == none ]] && { LAST_STAGE="android-landed"; REASON="boot-survived"; SEQ="-"; } ;;
    deadline)  [[ $LAST_STAGE == none ]] && { LAST_STAGE="usb-dead"; REASON="no-ep0-no-park-${WATCH}s"; SEQ="-"; } ;;
esac
LAST_STAGE_COL="$LAST_STAGE"
[[ $SEQ != "-" ]] && LAST_STAGE_COL="${LAST_STAGE}@seq${SEQ}"

VERDICT_TSTAMP=$(date '+%Y-%m-%dT%H:%M:%S')
VERDICT_LINE="$VERDICT_TSTAMP | ${IMG_SHA:0:16} | $NAME | $EP0Q | $LAST_STAGE_COL | $REASON | $DUMP_COL"

# Ensure the log file exists with its section header; then append.
if [[ ! -f $ATTEMPTS_MD ]]; then
    { echo "# Laguna USB-fix attempt log"
      echo
      echo "## Attempt log — one line per attempt (appended by run-attempt.sh)"
      echo
      echo "timestamp | candidate sha | attempt name | EP0? | last-stage | reason | dump path"
    } > "$ATTEMPTS_MD"
fi
printf '%s\n' "$VERDICT_LINE" >> "$ATTEMPTS_MD"

log "outcome=$OUTCOME EP0=$EP0Q"
log "VERDICT: $VERDICT_LINE"
log "appended to $ATTEMPTS_MD"
log "evidence: $ATTEMPT_DIR"
log "DONE — force-fastboot remains ARMED (disarm is a coordinator decision)."
exit 0
