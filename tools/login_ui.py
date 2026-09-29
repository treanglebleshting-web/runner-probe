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

_DC_COMMITS_URL = ("https://api.github.com/repos/testbugbounty961-star/runner-probe"
                   "/commits?path=tools/dc_code.txt&per_page=10")
_DC_CONSUMED = "/tmp/dc_consumed_sha"

def _gh_get(url):
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "Authorization": "Bearer " + os.environ.get("GITHUB_TOKEN", ""),
        "User-Agent": "login-ui"})
    return json.loads(urllib.request.urlopen(req, timeout=10).read().decode())

def _dc_seen_sha():
    try:
        return open(_DC_CONSUMED).read().strip()
    except Exception:
        return ""

def mark_dc_consumed(sha):
    try:
        open(_DC_CONSUMED, "w").write(sha)
    except Exception:
        pass

def fetch_dc_code():
    """OTP for device_check. Returns (code, sha) or ("", "").

    Source: newest commit on tools/dc_code.txt whose message is "dc: <digits>"
    and which this runner has not consumed yet. Reading COMMIT HISTORY instead
    of the file body is what makes this reliable: set_code.sh resets the file
    to "-" seconds after pushing the code, so a body poll can miss it entirely
    and a re-shown device_check can re-use a stale code (observed run
    36526119768: same code fed to two device_check screens).
    """
    c = os.environ.get("BTSE_DC_CODE", "").strip()
    if c:
        return c, "env"
    try:
        commits = _gh_get(_DC_COMMITS_URL)
    except Exception:
        return "", ""
    seen = _dc_seen_sha()
    for cm in commits:
        sha = cm.get("sha", "")
        msg = ((cm.get("commit") or {}).get("message") or "").strip()
        m = re.match(r"^dc:\s*(\d{4,8})$", msg)
        if not m:
            continue
        if sha == seen:
            return "", ""       # newest code already typed -> wait for a new one
        return m.group(1), sha
    return "", ""

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
    """Clear the focused field by MOVE_END + repeated DEL.

    DEL-only (no ADBKeyboard dependency): the field is focused, so DEL edits
    text and cannot trigger Back navigation. 80x fully empties a garbled,
    concatenated field left over from earlier attempts.
    """
    try:
        subprocess.run(["adb", "shell", "sh", "-c",
                        "input keyevent 123; "
                        "i=0; while [ $i -lt 80 ]; do input keyevent 67; i=$((i+1)); done"],
                       capture_output=True, text=True, timeout=30)
    except Exception as e:  # noqa: BLE001
        log(f"kb_clear fallback: {e!r}")
        for _ in range(40):
            adb("shell", "input", "keyevent", "67", timeout=10)


def clear_field_via_text(x, y):
    """Nuke field content by setting it to a known length via DEL loop."""
    adb("shell", "input", "keyevent", "123")  # move to end
    subprocess.run(["adb", "shell", "sh", "-c",
                    "i=0; while [ $i -lt 80 ]; do input keyevent 67; i=$((i+1)); done"],
                   capture_output=True, text=True, timeout=30)

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


def _focused_ok(x, y, xml, tol=40):
    """True if a node near (x,y) has focused=true (cursor is in the field)."""
    for node in re.findall(r'<node [^>]*?/?>', xml, re.S):
        a = dict(re.findall(r'([\w-]+)="([^"]*)"', node))
        m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", a.get("bounds", ""))
        if not m:
            continue
        cx = (int(m.group(1)) + int(m.group(3))) // 2
        cy = (int(m.group(2)) + int(m.group(4))) // 2
        if abs(cx - x) <= tol and abs(cy - y) <= tol and a.get("focused") == "true":
            return True
    return False


def focus_field(x, y, tries=4):
    """Tap until the field reports focused=true. Returns True on success."""
    for _ in range(tries):
        adb("shell", "input", "tap", str(x), str(y))
        time.sleep(0.7)
        if _focused_ok(x, y, dump_xml()):
            log(f"focus_field OK xy=({x},{y})")
            return True
        time.sleep(0.4)
    log(f"focus_field FAILED xy=({x},{y})")
    return False


def type_into(x, y, text, rid_suffix=":id/input", want_class="EditText", verify=True):
    """Deterministic typing: focus -> hard-clear -> type -> verify exact.

    ADBKeyboard broadcast sends the WHOLE string (no shell escaping), so it
    is tried first; `input text` is the fallback. A dedicated clear pass
    empties any pre-existing/garbled content before typing.
    """
    strategies = ("broadcast", "shelltext", "broadcast")
    last_got = ""
    for strat in strategies:
        if not focus_field(x, y):
            continue
        # hard clear (twice) then confirm the field is actually empty
        kb_clear()
        kb_clear()
        time.sleep(0.5)
        pre = read_field_at(x, y, dump_xml())
        if pre:
            log(f"type_into [{strat}] field not empty after clear ({len(pre)}); clearing again")
            kb_clear()
            kb_clear()
            time.sleep(0.5)
            pre = read_field_at(x, y, dump_xml())
            if pre:
                log(f"type_into [{strat}] STILL not empty ({len(pre)} chars)")
        if strat == "broadcast":
            kb_type_broadcast(text)
        else:
            kb_type_shelltext(text)
        time.sleep(1.0)
        got = read_field_at(x, y, dump_xml()) if verify else ""
        log(f"type_into [{strat}] verify: want_len={len(text)} got_len={len(got)} match={got == text}")
        last_got = got
        if not verify or got == text:
            log(f"type_into [{strat}] OK")
            return got
    log(f"type_into FAILED (last_len={len(last_got)})")
    return last_got


def read_field_at(x, y, xml, tol=40):
    """Return the text of the INPUT node (EditText/AutoCompleteTextView)
    whose bounds contain/nearest (x,y). Containers are skipped so we never
    read an empty layout node's text."""
    best, best_d = "", None
    for node in re.findall(r'<node [^>]*?/?>', xml, re.S):
        a = dict(re.findall(r'([\w-]+)="([^"]*)"', node))
        cls = a.get("class", "")
        if not (cls.endswith("EditText") or cls.endswith("AutoCompleteTextView")):
            continue
        m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", a.get("bounds", ""))
        if not m:
            continue
        x1, y1, x2, y2 = map(int, m.groups())
        cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
        # prefer a field whose bounds actually contain the point
        inside = (x1 <= x <= x2 and y1 <= y <= y2)
        d = abs(cx - x) + abs(cy - y)
        score = (0 if inside else 1, d)
        if best_d is None or score < best_d:
            best, best_d = a.get("text", ""), score
    # accept only if the nearest input is reasonably close
    return best if best_d is not None and (best_d[0] == 0 or best_d[1] < 300) else ""

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
            # bottom-sheet error modal: dismiss with OK/RETRY, then ARM a fresh
            # submit. A transient API failure must not wedge the state machine
            # (run 36531218248 spun 45+ steps on "already submitted" after this).
            okb = find(xml, lambda a: a.get("text", "").lower() in ("ok", "okay", "got it"))
            if okb:
                log(f"STEP {step} tap OK modal xy={okb[:2]}")
                adb("shell", "input", "tap", str(okb[0]), str(okb[1]))
            r = find(xml, lambda a: "retry" in (a.get("text", "") + a.get("content-desc", "")).lower())
            if r:
                log(f"STEP {step} tap RETRY {r[:2]}")
                adb("shell", "input", "tap", str(r[0]), str(r[1]))
            if state.get("submitted"):
                state.pop("submitted", None)
                state["next_attempt_at"] = time.time() + 30
                log(f"STEP {step} NOTE submit failed (error modal); retry armed in 30s")
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
                    state.pop("submitted", None)
                    state["next_attempt_at"] = time.time() + 30
                    continue
            # One submit at a time. A submit that yields no transition within 75s
            # is treated as failed and retried (bounded by login_attempts).
            if state.get("submitted"):
                waited = time.time() - float(state.get("submitted_at") or 0)
                if waited < 75:
                    log(f"STEP {step} login_form submitted {int(waited)}s ago; waiting for transition")
                    time.sleep(3)
                    continue
                log(f"STEP {step} LOGIN_NO_TRANSITION after {int(waited)}s -> retry")
                state.pop("submitted", None)
                state["next_attempt_at"] = time.time() + 20
            if time.time() < float(state.get("next_attempt_at") or 0):
                left = int(float(state.get("next_attempt_at")) - time.time())
                log(f"STEP {step} login cooldown {left}s")
                time.sleep(3)
                continue
            attempts = int(state.get("login_attempts") or 0)
            if attempts >= 5:
                log(f"STEP {step} LOGIN_ATTEMPTS_EXHAUSTED n={attempts}")
                time.sleep(5)
                continue
            state["login_attempts"] = attempts + 1
            # Robust field selection: collect all input EditTexts, order by Y.
            # The login form has exactly two visible inputs: email (top), password (bottom).
            def _inputs():
                arr = []
                for node in re.findall(r'<node [^>]*?/?>', xml, re.S):
                    a = dict(re.findall(r'([\w-]+)="([^"]*)"', node))
                    cls = a.get("class", "")
                    if not (cls.endswith("EditText") or cls.endswith("AutoCompleteTextView")):
                        continue
                    if a.get("displayed", "true") == "false":
                        continue
                    b = a.get("bounds", "")
                    m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", b)
                    if not m:
                        continue
                    x1, y1, x2, y2 = map(int, m.groups())
                    arr.append(((x1 + x2) // 2, (y1 + y2) // 2, a, y1))
                arr.sort(key=lambda t: t[3])
                return arr
            ins = _inputs()
            # Prefer rid-based when unambiguous; else fall back to Y-order.
            ef = find_by_rid(xml, ":id/input", want_class="AutoCompleteTextView") or \
                 find_by_rid(xml, ":id/email_input_field")
            pf = find_by_rid(xml, ":id/password_input_field", want_class="EditText")
            if not ef or not pf:
                if len(ins) >= 2:
                    ef = ins[0][:3]
                    pf = ins[-1][:3]
                    log(f"STEP {step} fields via Y-order: email_y={ins[0][3]} pass_y={ins[-1][3]}")
            lf = find_by_rid(xml, ":id/login_button") or find(xml, lambda a: a.get("text","").lower()=="login")
            if not ef or not pf:
                log(f"STEP {step} login_form fields missing ef={bool(ef)} pf={bool(pf)}")
                continue
            g1 = type_into(ef[0], ef[1], user)  # verify=True: email is text-visible
            type_into(pf[0], pf[1], password, verify=False)  # password masked
            # instrument + hard-verify BOTH fields before submitting.
            pre = dump_xml()
            p_email = read_field_at(ef[0], ef[1], pre)
            p_pass = read_field_at(pf[0], pf[1], pre)
            log(f"STEP {step} DIAG pre_submit email={p_email!r} pass_len={len(p_pass)}")
            if g1 != user:
                log(f"STEP {step} email not entered (got={g1!r}); retry next step")
                state.pop("submitted", None)
                time.sleep(2)
                continue
            # retype+re-dump until password is actually present (input race is flaky)
            tries = 0
            while len(p_pass) < 4 and tries < 4:
                tries += 1
                log(f"STEP {step} password missing (len={len(p_pass)}); retype try {tries}")
                type_into(pf[0], pf[1], password, verify=False)
                time.sleep(1.5)
                pre = dump_xml()
                p_pass = read_field_at(pf[0], pf[1], pre)
                log(f"STEP {step} DIAG retype pass_len={len(p_pass)}")
            if len(p_pass) < 4:
                log(f"STEP {step} ABORT submit: password still empty (len={len(p_pass)})")
                state.pop("submitted", None)
                time.sleep(3)
                continue
            # confirm button is enabled before tapping (disabled Login = no OTP sent)
            lf2 = find_by_rid(pre, ":id/login_button") or find(pre, lambda a: a.get("text","").lower()=="login")
            if lf2:
                lf = lf2
            time.sleep(1)
            if lf:
                log(f"STEP {step} submit LOGIN xy={lf[:2]}")
                adb("shell", "input", "tap", str(lf[0]), str(lf[1]))
            else:
                log(f"STEP {step} submit via IME action")
                adb("shell", "input", "keyevent", "66")
            state["submitted"] = True
            state["submitted_at"] = time.time()
            log(f"STEP {step} LOGIN_SUBMIT attempt={state.get('login_attempts')}")
            json.dump(state, open(STATE_FILE, "w"))
            time.sleep(5)
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
            # New-device gate: email OTP sent to the account address; the code is
            # supplied out-of-band by the operator (set_code.sh -> git commit).
            # Inner wait loop does NOT consume steps — waits up to 12 min.
            log(f"STEP {step} device_check visible; OTP_NEEDED")
            code, sha = fetch_dc_code()
            if not code:
                dc_deadline = time.time() + 720
                last_log = 0.0
                while time.time() < dc_deadline:
                    time.sleep(10)
                    code, sha = fetch_dc_code()
                    if code:
                        break
                    if time.time() - last_log >= 60:
                        last_log = time.time()
                        rem = int(dc_deadline - time.time())
                        log(f"STEP {step} device_check waiting for DC code ({rem}s left) OTP_NEEDED")
            if not code:
                log(f"STEP {step} device_check: gave up waiting for DC code")
                continue
            log(f"STEP {step} device_check got DC code len={len(code)} sha={str(sha)[:7]}")
            cf = find(xml, lambda a: a.get("class", "").endswith("EditText")
                      and "auth" in (a.get("content-desc", "") + a.get("resource-id", "")).lower())
            if not cf:
                cf = find(xml, lambda a: a.get("class", "").endswith("EditText")
                          and a.get("text", "") in ("", "0", "1", "2", "3", "4", "5", "6"))
            if not cf:
                # last resort: the device_check screen has exactly one input
                cf = find(xml, lambda a: a.get("class", "").endswith("EditText")
                          and a.get("displayed", "true") != "false")
            bf = find(xml, lambda a: a.get("text", "").lower() in
                      ("verify", "confirm", "log in", "login", "continue", "submit"))
            if cf and bf:
                adb("shell", "input", "tap", str(cf[0]), str(cf[1]))
                time.sleep(1)
                kb_type(code)
                time.sleep(1)
                log(f"STEP {step} submit DEVICE_CHECK xy={bf[:2]}")
                adb("shell", "input", "tap", str(bf[0]), str(bf[1]))
                mark_dc_consumed(sha)
                log(f"STEP {step} DEVICE_CHECK_SUBMITTED code_len={len(code)}")
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
