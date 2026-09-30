#!/bin/bash
# Dispatch probe and monitor steps until done.
cd "$(dirname "$0")/.."
printf -- '-' > tools/dc_code.txt
git add tools/dc_code.txt 2>/dev/null && git -c user.name=poc -c user.email=poc@local commit -m "dc: reset" -q || true
git push -q origin main || true
gh workflow run probe.yml -R testbugbounty961-star/runner-probe
sleep 8
RID=$(gh run list -R testbugbounty961-star/runner-probe --limit 1 --json databaseId -q '.[0].databaseId')
echo "RUN=$RID"
echo "Monitor: gh run view $RID -R testbugbounty961-star/runner-probe"
echo "Log   : gh run view $RID -R testbugbounty961-star/runner-probe --log | grep -E 'DIAG|device_check|got DC|VERDICT|STABLE'"
echo
echo ">>> SUBMIT OTP with: ./tools/set_code.sh <CODE>   (when the UI login step runs >3 minutes)"
