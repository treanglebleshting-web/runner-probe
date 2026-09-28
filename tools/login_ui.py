#!/usr/bin/env python3
"""Drive BTSE app login UI via adb uiautomator + input.

Deterministic: dump UI XML, locate nodes by text/hint, tap, type, screenshot.
No hard sleep except settle waits; re-dumps after each transition.

Usage: login_ui.py <state_file> [max_steps]
  state_file: JSON {"step": int, ...} persisted across stage re-runs.
Env: BTSE_USER, BTSE_EMAIL, BTSE_PASSWORD, BTSE_2FA (optional override code)
"""
import json, os, re, subprocess, sys, time, tempfile, urllib.request
import base64, hmac, struct

def live_totp(secret, t=None, step=30, digits=6):
    key = base64.b32decode(secret.upper() + "=" * ((8 - len(secret) % 8) % 8))
    counter = int((t if t is not None else time.time()) // step)
    mac = hmac.new(key, struct.pack(">Q", counter), "sha1").digest()
    off = mac[-1] & 0x0F
    return str((struct.unpack(">I", mac[off:off + 4])[0] & 0x7FFFFFFF) % (10 ** digits)).zfill(digits)

PKG = "com.btse.finance"
MAX_STEPS = int(sys.argv[2]) if len(sys.argv) > 2 else 14
STATE_FILE = sys.argv[1]

# ADBKeyboard IME (deterministic input, exact string, no shell escaping)
ADBKB = "com.android.adbkeyboard/.AdbIME"

from typing import Dict, Optional
_kb_state: Dict[str, Optional[bool]] = {"avail": None}  # lazy detect

def adb_kb_available():
    """True if ADBKeyboard IME is installed & enabled (cached)."""
    if _kb_state["avail"] is None:
        try:
            r = subprocess.run(["adb", "shell", "ime", "list", "-a"],
                               capture_output=True, text=True, timeout=15)
            _kb_state["avail"] = ("com.android.adbkeyboard" in r.stdout)
        except Exception:
            _kb_state["avail"] = False
    return bool(_kb_state["avail"])

def kb_ensure():
    """Self-heal: make sure ADBKeyboard is enabled & active. Best-effort."""
    try:
        subprocess.run(["adb", "shell", "ime", "enable", ADBKB],
                       capture_output=True, text=True, timeout=20)
        time.sleep(1)
        subprocess.run(["adb", "shell", "ime", "set", ADBKB],
                       capture_output=True, text=True, timeout=20)
    except Exception:
        pass
    cur = subprocess.run(["adb", "shell", "settings", "get", "secure", "input_method"],
                         capture_output=True, text=True, timeout=15)
    log(f"kb_ensure: input_method={cur.stdout.strip()!r}")

def adb(*a, timeout=30):
    try:
        return subprocess.run(["adb", *a], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        log(f"ADB_TIMEOUT: {' '.join(a)}")
        return subprocess.CompletedProcess(["adb", *a], 124, "", "timeout")
    except Exception as e:  # noqa: BLE001
        log(f"ADB_ERROR: {e!r}")
        return subprocess.CompletedProcess(["adb", *a], 125, "", repr(e))

def kb_type(text):
    """Exact-string input via ADBKeyboard broadcast; fallback to input text."""
    try:
        if adb_kb_available():
            adb("shell", "am", "broadcast", "-a", "ADB_INPUT_TEXT",
                "--es", "msg", text, timeout=20)
            return "ime"
    except Exception:  # noqa: BLE001
        pass
    adb("shell", "input", "text", text)
    return "input_text"

def kb_clear():
    """Clear the focused field deterministically via backspaces (no broadcast)."""
    # single shell loop (fast); backspaces clear any field length in this flow.
    try:
        subprocess.run(["adb", "shell", "sh", "-c",
                        "input keyevent KEYCODE_MOVE_END; i=0; while [ $i -lt 40 ]; do input keyevent 67; i=$((i+1)); done"],
                       capture_output=True, text=True, timeout=40)
    except Exception as e:  # noqa: BLE001
        log(f"kb_clear fallback: {e!r}")
        for _ in range(20):
            adb("shell", "input", "keyevent", "67", timeout=10)

def dump_xml():
    adb("shell", "uiautomator", "dump", "/sdcard/ui.xml")
    adb("pull", "/sdcard/ui.xml", "/tmp/ui_login.xml")
    try:
        return open("/tmp/ui_login.xml", errors="ignore").read()
    except Exception:
        return ""

def find_by_rid(xml, rid_suffix, want_class=None, nth=0):
    """Find nodes by resource-id suffix; returns list of (x,y,attrs)."""
    out = []
    for node in re.findall(r'<node [^>]*?/?>', xml, re.S):
        a = dict(re.findall(r'([\w-]+)="([^"]*)"', node))
        if not a.get("resource-id", "").endswith(rid_suffix):
            continue
        if want_class and not a.get("class", "").endswith(want_class):
            continue
        b = a.get("bounds", "")
        m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", b)
        if m:
            x1, y1, x2, y2 = map(int, m.groups())
            out.append(((x1 + x2) // 2, (y1 + y2) // 2, a))
    return out[nth] if len(out) > nth else None


def find(xml, *preds):
    """Return (x,y,attrs) for first node matching any predicate."""
    nodes = re.findall(r'<node [^>]*?/?>', xml, re.S)
    for node in nodes:
        attrs = dict(re.findall(r'([\w-]+)="([^"]*)"', node))
        for p in preds:
            if p(attrs):
                b = attrs.get("bounds", "")
                m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", b)
                if m:
                    x1, y1, x2, y2 = map(int, m.groups())
                    return ((x1 + x2) // 2, (y1 + y2) // 2, attrs)
    return None

def state_of(xml):
    s = xml.lower()
    if "unable to complete" in s:
        return "net_error"
    if "two-factor" in s or "google auth" in s or ("2fa" in s and "code" in s):
        return "2fa"
    if "sign in with" in s or "another way" in s or "continue with" in s:
        return "login_sheet"
    # new-device check: single OTP/passcode input (before any home markers)
    if "new device" in s or "verify device" in s or ("device" in s and "verification" in s):
        return "device_check"
    home_like = ("assets" in s or "markets" in s or "withdraw" in s or "deposit" in s)
    login_marker = "log in" in s or "login" in s or "sign in" in s or "password" in s
    if home_like:
        # guest home ALSO shows markets/deposit — logged-in iff no login CTA visible
        return "guest_home" if login_marker else "home"
    if "log in to btse" in s or "log in" in s or "password" in s:
        return "login_form"
    return "unknown"

def log(msg):
    print(msg, flush=True)


def clear_field(x, y):
    """Focus field and empty it (deterministic, idempotent)."""
    adb("shell", "input", "tap", str(x), str(y))
    time.sleep(0.4)
    kb_clear()
    time.sleep(0.4)


def kb_type_broadcast(text):
    adb("shell", "am", "broadcast", "-a", "ADB_INPUT_TEXT",
        "--es", "msg", text, timeout=20)


def kb_type_shelltext(text):
    """Fallback: `input text` with %s-escaping (no IME dependency)."""
    esc = text.replace(" ", "%s").replace("&", "\\&").replace("(", "\\(").replace(")", "\\)")
    adb("shell", "input", "text", esc, timeout=20)


def type_into(x, y, text, rid_suffix=":id/input", want_class="EditText", verify=True):
    """Deterministic typing: `input text` (escaped) is primary.

    ADBKeyboard IME is unreliable here (broadcasts silently drop when the
    IME isn't the input connection). `input text` is the native injection
    path and works for any string (escaped). Strategy order:
      1. input text (escaped)
      2. ADBKeyboard broadcast
      3. input text again
    """
    strategies = ("shelltext", "broadcast", "shelltext")
    last_got = ""
    for strat in strategies:
        clear_field(x, y)
        if strat == "broadcast":
            kb_type_broadcast(text)
        else:
            kb_type_shelltext(text)
        time.sleep(0.8)
        got = read_field_at(x, y, dump_xml()) if verify else ""
        log(f"type_into [{strat}] verify: want={text!r} got={got!r}")
        last_got = got
        if not verify or got == text:
            log(f"type_into [{strat}] OK")
            return got
    log(f"type_into FAILED got={last_got!r}")
    return last_got


def read_field_at(x, y, xml, tol=30):
    best, best_d = "", None
    for node in re.findall(r'<node [^>]*?/?>', xml, re.S):
        a = dict(re.findall(r'([\w-]+)="([^"]*)"', node))
        m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", a.get("bounds", ""))
        if m:
            cx, cy = (int(m.group(1)) + int(m.group(3))) // 2, (int(m.group(2)) + int(m.group(4))) // 2
            d = abs(cx - x) + abs(cy - y)
            if abs(cx - x) < tol and abs(cy - y) < tol:
                return a.get("text", "")
            if "input" in a.get("resource-id", "") and (best_d is None or d < best_d):
                best, best_d = a.get("text", ""), d
    # fallback: nearest input node within 250px (handles tap/center drift)
    return best if best_d is not None and best_d < 250 else ""

def main():
    user = os.environ.get("BTSE_USER", "btleo8847")
    password = os.environ["BTSE_PASSWORD"]
    two_fa_override = os.environ.get("BTSE_2FA", "")  # static override (e.g. program 123456)
    totp_secret = os.environ.get("BTSE_2FA_SECRET", "")  # live TOTP source
    def two_fa_code():
        if two_fa_override:
            return two_fa_override
        if totp_secret:
            return live_totp(totp_secret)
        return "123456"

    state: dict = {"step": 0}
    if os.path.exists(STATE_FILE):
        try:
            state = json.load(open(STATE_FILE))
        except Exception:
            pass

    # self-heal ADBKeyboard IME before any input
    kb_ensure()

    for step in range(state.get("step", 0), MAX_STEPS):
        time.sleep(4)  # settle
        xml = dump_xml()
        st = state_of(xml)
        log(f"STEP {step} state={st}")
        state["step"] = step + 1
        state["last_state"] = str(st)
        json.dump(state, open(STATE_FILE, "w"))

        if st == "home":
            log("LOGIN_UI_DONE state=home")
            return
        if st == "guest_home":
            # guest: tap the "Login" CTA (top-right) to open the login form
            lb = find(xml, lambda a: a.get("text", "").lower() in ("login", "log in", "sign in"))
            if lb:
                log(f"STEP {step} tap LOGIN_CTA xy={lb[:2]}")
                adb("shell", "input", "tap", str(lb[0]), str(lb[1]))
            else:
                log(f"STEP {step} guest_home but no login CTA; back/tap profile area")
                adb("shell", "input", "tap", "1050", "150")
            continue
        if st == "net_error":
            # bottom-sheet error modal: dismiss with OK, then retry
            okb = find(xml, lambda a: a.get("text", "").lower() in ("ok", "okay", "got it"))
            if okb:
                log(f"STEP {step} tap OK modal xy={okb[:2]}")
                adb("shell", "input", "tap", str(okb[0]), str(okb[1]))
            r = find(xml, lambda a: "retry" in (a.get("text", "") + a.get("content-desc", "")).lower())
            if r:
                log(f"STEP {step} tap RETRY {r[:2]}")
                adb("shell", "input", "tap", str(r[0]), str(r[1]))
            continue
        if st == "login_form":
            # if an error bottom-sheet covers the form, dismiss it first
            errm = find(xml, lambda a: "unable to complete" in (a.get("text", "") + a.get("content-desc", "")).lower())
            okm = find(xml, lambda a: a.get("text", "").lower() in ("ok", "okay", "got it"))
            if errm or okm:
                t = okm if okm else errm
                if t:
                    log(f"STEP {step} dismiss error modal xy={t[:2]}")
                    adb("shell", "input", "tap", str(t[0]), str(t[1]))
                    time.sleep(2)
                    continue
            # Both email + password use rid ':id/input' -> distinguish by class.
            ef = find_by_rid(xml, ":id/input", want_class="AutoCompleteTextView") or \
                 find_by_rid(xml, ":id/email_input_field")
            pf = find_by_rid(xml, ":id/password_input_field", want_class="EditText") or \
                 find_by_rid(xml, ":id/input", want_class="EditText")
            lf = find_by_rid(xml, ":id/login_button") or find(xml, lambda a: a.get("text","").lower()=="login")
            if not ef or not pf:
                log(f"STEP {step} login_form fields missing ef={bool(ef)} pf={bool(pf)}")
                continue
            type_into(ef[0], ef[1], user)  # verify=True: email is text-visible
            type_into(pf[0], pf[1], password, verify=False)  # password masked
            if lf:
                log(f"STEP {step} submit LOGIN xy={lf[:2]}")
                adb("shell", "input", "tap", str(lf[0]), str(lf[1]))
            else:
                log(f"STEP {step} submit via IME action")
                adb("shell", "input", "keyevent", "66")
            continue
        if st == "login_sheet":
            # app sheet: Email / Google / Phone / OTP tabs -> pick Email, tap continue
            em = find(xml, lambda a: a.get("text", "").lower() in ("email", "log in with email"))
            if em:
                log(f"STEP {step} tap EMAIL xy={em[:2]}")
                adb("shell", "input", "tap", str(em[0]), str(em[1]))
                continue
            ct = find(xml, lambda a: "continue" in a.get("text", "").lower())
            if ct:
                log(f"STEP {step} tap CONTINUE xy={ct[:2]}")
                adb("shell", "input", "tap", str(ct[0]), str(ct[1]))
                continue
        if st == "device_check":
            # new-device gate: email OTP sent to account email; code supplied
            # out-of-band by the operator (BTSE_DC_CODE env / secret).
            code = os.environ.get("BTSE_DC_CODE", "")
            if not code:
                log(f"STEP {step} device_check: no BTSE_DC_CODE set; wait for operator")
                continue
            cf = find(xml, lambda a: a.get("class", "").endswith("EditText")
                      and "auth" in (a.get("content-desc", "") + a.get("resource-id", "")).lower())
            if not cf:
                cf = find(xml, lambda a: a.get("class", "").endswith("EditText")
                          and a.get("text", "") in ("", "0", "1", "2", "3", "4", "5", "6"))
            bf = find(xml, lambda a: a.get("text", "").lower() in
                      ("verify", "confirm", "log in", "login", "continue", "submit"))
            if cf and bf:
                adb("shell", "input", "tap", str(cf[0]), str(cf[1]))
                time.sleep(1)
                kb_type(code)
                time.sleep(1)
                log(f"STEP {step} submit DEVICE_CHECK xy={bf[:2]}")
                adb("shell", "input", "tap", str(bf[0]), str(bf[1]))
            else:
                log(f"STEP {step} device_check fields missing codef={bool(cf)} btn={bool(bf)}")
        if st == "2fa":
            # stateful: attempt N uses a different code source to recover from a wrong-code screen
            att = state.get("tfa_attempts", 0) + 1
            state["tfa_attempts"] = att
            src = "override" if (att % 2 == 1 and two_fa_override) else ("totp" if totp_secret else "override")
            code = two_fa_override if src == "override" else live_totp(totp_secret)
            log(f"STEP {step} 2FA attempt={att} source={src} code_len={len(code)}")
            codef = find(xml, lambda a: a.get("class", "").endswith("EditText") and "auth" in a.get("content-desc", "").lower())
            if not codef:
                codef = find(xml, lambda a: a.get("class", "").endswith("EditText") and a.get("text", "") in ("", "0", "1", "2", "3", "4", "5", "6"))
            bf = find(xml, lambda a: a.get("text", "").lower() in ("verify", "confirm", "log in", "login", "continue"))
            if codef and bf:
                adb("shell", "input", "tap", str(codef[0]), str(codef[1]))
                time.sleep(1)
                kb_type(code)
                time.sleep(1)
                log(f"STEP {step} submit 2FA xy={bf[:2]}")
                adb("shell", "input", "tap", str(bf[0]), str(bf[1]))
            else:
                log(f"STEP {step} 2fa fields missing codef={bool(codef)} btn={bool(bf)}")
        # unknown/signup: screenshot for forensics, try close/back
        adb("shell", "screencap", "-p", "/sdcard/ui_state.png")
        adb("pull", "/sdcard/ui_state.png", f"/tmp/ui_state_{step}.png")
        log(f"STEP {step} state={st} saved /tmp/ui_state_{step}.png")
        adb("shell", "input", "keyevent", "BACK")

    log("LOGIN_UI_TIMEOUT")

if __name__ == "__main__":
    main()
