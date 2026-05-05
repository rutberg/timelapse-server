#!/usr/bin/env bash
# Simulates a DSLR agent (gphoto2 backend) sending heartbeats to the dev server.
# Includes a realistic DSLR payload (battery, lens, choices, current values, init token)
# so the camera Settings tab can be exercised end-to-end without real hardware.
#
# Usage: ./scripts/simulate-dslr.sh [camera_id] [server_url] [interval_seconds]
set -euo pipefail

CAMERA_ID="${1:-dslr-test}"
SERVER="${2:-http://127.0.0.1:8080}"
INTERVAL="${3:-30}"

AGENT_VERSION="0.9.0"
HOSTNAME="canon-r6-sim"
SIGNAL_DBM=-62

echo "Simulating DSLR agent for camera '$CAMERA_ID' → $SERVER (checkin every ${INTERVAL}s)"
echo "Press Ctrl-C to stop."

pending_count=0
last_capture_at=""
last_upload_at=""
last_init_at="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
# Track which reinit_token we last "applied" so the simulator behaves like a
# real agent: when the saved config gets a new reinit_token, we wait one tick
# and then echo it back as last_reinit_token + bump last_init_at.
last_applied_token=""

while true; do
    local_hour=$(date +%-H)
    now=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

    # Simulate a capture every ~3 checkins
    if (( RANDOM % 3 == 0 )); then
        last_capture_at="$now"
        pending_count=$(( pending_count + 1 ))
    fi

    # Simulate an upload clearing the queue every ~5 checkins
    if (( pending_count > 0 && RANDOM % 5 == 0 )); then
        last_upload_at="$now"
        pending_count=0
    fi

    pending_bytes=$(( pending_count * 8000000 ))

    # Daylight hours: in_schedule true between 06:00-20:00 local
    if (( local_hour >= 6 && local_hour < 20 )); then
        in_schedule="true"
    else
        in_schedule="false"
    fi

    # Pull the latest config so we can see if the user has triggered a reinit.
    cfg_json="$(curl -s "$SERVER/api/cameras/$CAMERA_ID/config" || echo '{}')"
    pending_token="$(printf '%s' "$cfg_json" | python3 -c 'import json,sys
try:
    d = json.load(sys.stdin)
    print((d.get("dslr") or {}).get("reinit_token") or "")
except Exception:
    print("")')"

    if [[ -n "$pending_token" && "$pending_token" != "$last_applied_token" ]]; then
        # Pretend the camera took a beat to apply the new init.
        sleep 1
        last_applied_token="$pending_token"
        last_init_at="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
        echo "  ↻ applied reinit_token=$pending_token"
    fi

    last_reinit_field="null"
    if [[ -n "$last_applied_token" ]]; then
        last_reinit_field="\"$last_applied_token\""
    fi

    # A reasonably representative Canon R6 fixture.
    payload=$(cat <<JSON
{
  "agent_version": "$AGENT_VERSION",
  "hostname": "$HOSTNAME",
  "last_capture_at": $([ -n "$last_capture_at" ] && echo "\"$last_capture_at\"" || echo "null"),
  "last_upload_at": $([ -n "$last_upload_at" ] && echo "\"$last_upload_at\"" || echo "null"),
  "last_error": null,
  "pending_count": $pending_count,
  "pending_bytes": $pending_bytes,
  "in_schedule": $in_schedule,
  "local_hour": $local_hour,
  "signal_dbm": $SIGNAL_DBM,
  "active_backend": "gphoto2",
  "dslr": {
    "battery_level": "75%",
    "available_shots": 1842,
    "shutter_counter": 23117,
    "exposure_mode": "M",
    "lens_name": "RF24-105mm F4 L IS USM",
    "choices": {
      "shutterspeed": ["bulb","30","15","8","4","2","1","1/2","1/4","1/8","1/15","1/30","1/60","1/125","1/250","1/500","1/1000","1/2000","1/4000","1/8000"],
      "aperture": ["4","4.5","5","5.6","6.3","7.1","8","9","10","11","13","14","16","18","20","22"],
      "iso": ["Auto","100","200","400","800","1600","3200","6400","12800","25600","51200"],
      "exposurecompensation": ["-3","-2","-1","-0.6667","-0.3333","0","0.3333","0.6667","1","2","3"],
      "whitebalance": ["Auto","Daylight","Cloudy","Tungsten","Fluorescent","Flash","Custom","Shade","Color Temperature"],
      "imageformat": ["Large Fine JPEG","Large Normal JPEG","RAW","RAW + Large Fine JPEG","cRAW","cRAW + Large Fine JPEG"],
      "capturetarget": ["Internal RAM","Memory card"],
      "drivemode": ["Single","Continuous","Self Timer 2 sec","Self Timer 10 sec"],
      "focusmode": ["Manual","One Shot","AI Servo"]
    },
    "current_values": {
      "shutterspeed": "1/125",
      "aperture": "5.6",
      "iso": "400",
      "exposurecompensation": "0",
      "whitebalance": "Daylight",
      "imageformat": "Large Fine JPEG",
      "capturetarget": "Memory card",
      "drivemode": "Single",
      "focusmode": "Manual"
    },
    "last_reinit_token": $last_reinit_field,
    "last_init_at": "$last_init_at"
  }
}
JSON
)

    response=$(curl -s -o /dev/null -w "%{http_code}" \
        -X POST "$SERVER/api/cameras/$CAMERA_ID/checkin" \
        -H "Content-Type: application/json" \
        -d "$payload")

    echo "[$(date -u +%H:%M:%S)] checkin → HTTP $response  pending=$pending_count  in_schedule=$in_schedule"

    sleep "$INTERVAL"
done
