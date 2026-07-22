#!/bin/sh
set -eu

socket=${YDOTOOL_SOCKET:?YDOTOOL_SOCKET is required}
ydotoold_pid=${MOZKEY_DOGFOOD_YDOTOOLD_PID:?MOZKEY_DOGFOOD_YDOTOOLD_PID is required}
script_path=$(/usr/bin/realpath -- "$0") || {
  echo "could not resolve the tracked sequence helper" >&2
  exit 1
}
script_dir=$(CDPATH= cd -- "$(dirname -- "${script_path}")" && pwd -P)
usage='usage: send_ime_sequence.sh ROMAJI_READING [direct|conversion|literal|password]'
reading=${1:?${usage}}
mode=${2:-conversion}
input_method=mozkey-ibg
key_delay=${MOZKEY_DOGFOOD_KEY_DELAY_MS:-50}
settle_delay=${MOZKEY_DOGFOOD_SETTLE_DELAY_SECONDS:-1}
verifier=${script_dir}/verify_ydotool_socket.py
verified_runner=${script_dir}/run_verified_ydotool.py

if [ ! -f "${verifier}" ] || [ -L "${verifier}" ] || \
  [ ! -f "${verified_runner}" ] || [ -L "${verified_runner}" ]; then
  echo "the tracked ydotool verifier is unavailable" >&2
  exit 1
fi

case "${input_method}" in
  ''|*[!A-Za-z0-9_.@+-]*)
    echo "input method identifier is invalid" >&2
    exit 2
    ;;
esac

case "${settle_delay}" in
  0|1|2|3|4|5) ;;
  *)
    echo "settle delay must be an integer from 0 through 5" >&2
    exit 2
    ;;
esac

case "${mode}" in
  direct|conversion|literal|password) ;;
  *)
    echo "mode must be direct, conversion, literal, or password" >&2
    exit 2
    ;;
esac

fcitx_remote() {
  /usr/bin/timeout --foreground --kill-after=1s 5s \
    /usr/bin/fcitx5-remote "$@"
}

verify_socket() {
  /usr/bin/timeout --foreground --kill-after=1s 5s \
    /usr/bin/python3 -I "${verifier}" --socket "${socket}" --pid "${ydotoold_pid}"
}

run_ydotool() {
  /usr/bin/timeout --foreground --kill-after=1s 15s \
    /usr/bin/python3 -I "${verified_runner}" \
    --socket "${socket}" --pid "${ydotoold_pid}" --timeout-seconds 10 -- "$@"
}

send_key() {
  run_ydotool key "$1:1" "$1:0"
}

send_type() {
  run_ydotool type --key-delay "${key_delay}" "${reading}"
}

settle() {
  /usr/bin/sleep "${settle_delay}"
}

capture_fcitx_snapshot() {
  snapshot_state_1=$(fcitx_remote) || return 1
  snapshot_im_1=$(fcitx_remote -n) || return 1
  snapshot_im_2=$(fcitx_remote -n) || return 1
  snapshot_state_2=$(fcitx_remote) || return 1

  case "${snapshot_im_1}" in
    ''|*[!A-Za-z0-9_.@+-]*) return 1 ;;
  esac
  case "${snapshot_state_1}" in
    1|2) ;;
    *) return 1 ;;
  esac
  if [ "${snapshot_im_1}" != "${snapshot_im_2}" ] || \
    [ "${snapshot_state_1}" != "${snapshot_state_2}" ]; then
    return 1
  fi

  captured_im=${snapshot_im_1}
  captured_state=${snapshot_state_1}
}

verify_socket
if ! capture_fcitx_snapshot; then
  echo "could not capture a stable focused Fcitx identity" >&2
  exit 1
fi
previous_im=${captured_im}
previous_state=${captured_state}
readonly previous_im previous_state

restore_state() {
  original_status=$?
  trap - EXIT HUP INT TERM
  restore_status=0
  fcitx_remote -s "${previous_im}" >/dev/null 2>&1 || restore_status=1
  if [ "${previous_state}" = '2' ]; then
    fcitx_remote -o >/dev/null 2>&1 || restore_status=1
  else
    fcitx_remote -c >/dev/null 2>&1 || restore_status=1
  fi
  if [ "${restore_status}" -eq 0 ]; then
    if ! capture_fcitx_snapshot || \
      [ "${captured_im}" != "${previous_im}" ] || \
      [ "${captured_state}" != "${previous_state}" ]; then
      restore_status=1
    fi
  fi
  if [ "${restore_status}" -ne 0 ]; then
    echo "failed to restore the prior Fcitx identity" >&2
  fi
  if [ "${original_status}" -ne 0 ]; then
    exit "${original_status}"
  fi
  exit "${restore_status}"
}
trap restore_state EXIT
trap 'exit 130' HUP INT TERM

fcitx_remote -s "${input_method}"
fcitx_remote -o
if ! capture_fcitx_snapshot || \
  [ "${captured_im}" != "${input_method}" ] || \
  [ "${captured_state}" != '2' ]; then
  echo "could not establish the requested Fcitx identity" >&2
  exit 1
fi
send_type
settle

case "${mode}" in
  literal|password)
    send_key 28
    ;;
  direct)
    send_key 57
    settle
    send_key 28
    ;;
  conversion)
    send_key 57
    settle
    send_key 28
    settle
    send_key 28
    ;;
esac
