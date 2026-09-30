#!/bin/bash
# Usage: ./tools/set_code.sh 123456
# Commit OTP to dc_code.txt so login_ui (currently running) picks it up within <=10s.
set -e
cd "$(dirname "$0")/.."
[ -z "$1" ] && { echo "usage: $0 <OTP>"; exit 1; }
printf '%s' "$1" > tools/dc_code.txt
git add tools/dc_code.txt
git -c user.name=poc -c user.email=poc@local commit -m "dc: $1" -q
git push -q origin main
printf -- '-' > tools/dc_code.txt
git add tools/dc_code.txt
git -c user.name=poc -c user.email=poc@local commit -m "dc: reset" -q
git push -q origin main
echo "OTP $1 committed at $(date -u +%H:%M:%S) UTC"
