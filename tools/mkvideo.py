#!/usr/bin/env python3
"""Gabungkan video layar HP (adb screenrecord) dengan log penyerang (ber-timestamp)
menjadi video PoC side-by-side: [HP | terminal penyerang].

Pakai:
  python3 mkvideo.py --log timed.log --phone phone.mp4 --out final.mp4 [--t0 HH:MM:SS]

Input log: setiap baris diawali [HH:MM:SS] (dihasilkan oleh pipe timestamp).
Baris berdekatan (<1.5 dtk) dikelompokkan jadi satu event; event tampil dari
waktunya sampai event berikutnya (event terakhir tampil sampai akhir video).

Dependensi: ffmpeg. Output: H.264, tanpa audio (screenrecord tanpa audio).
"""
import argparse
import re
import shutil
import subprocess
import sys
import tempfile

TS_RE = re.compile(r"^\[(\d{2}):(\d{2}):(\d{2})\]\s?(.*)$")


def sec(hms):
    h, m, s = (int(x) for x in hms.split(":"))
    return h * 3600 + m * 60 + s


def fmt(t):
    if t < 0:
        t = 0
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t % 60
    return "%d:%02d:%05.2f" % (h, m, s)


def load_events(path, t0):
    events = []  # [(start_sec, [lines...])]
    cur = None
    last = None
    for raw in open(path, errors="replace"):
        s = raw.rstrip("\n").strip()
        if not s or set(s) == {"="}:
            continue
        m = TS_RE.match(s)  # strip dulu: baris banner ber-indentasi ("  [hh:mm:ss] ...")
        if not m:
            if cur:  # baris lanjutan tanpa ts -> gabung
                cur[1].append(s)
            continue
        t = sec("%s:%s:%s" % m.group(1, 2, 3)) - t0
        if t < -10:  # event dari ronde sebelumnya (di luar rentang rekaman) -> buang
            continue
        line = m.group(4)
        if last is not None and t - last < 1.5 and cur is not None:
            cur[1].append(line)
        else:
            cur = [t, [line]]
            events.append(cur)
        last = t
    return [e for e in events if e[1]]


def build_ass(events, dur, out):
    hdr = """[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Term,DejaVu Sans Mono,22,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,1,0,7,10,10,10,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [hdr]
    n = len(events)
    for i, (start, texts) in enumerate(events):
        end = events[i + 1][0] if i + 1 < n else dur + 1
        if end <= start:
            end = start + 0.5
        texts = [t.replace("{", "(").replace("}", ")") for t in texts][:26]
        body = "\\N".join(texts)
        lines.append(
            "Dialogue: 0,%s,%s,Term,,0,0,0,,{\\pos(512,24)}%s"
            % (fmt(max(start, 0)), fmt(end), body)
        )
    open(out, "w").write("\n".join(lines) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", required=True)
    ap.add_argument("--phone", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--t0", default="00:00:00", help="wall-clock mulai rekam HP (HH:MM:SS)")
    a = ap.parse_args()

    if not shutil.which("ffmpeg"):
        sys.exit("ffmpeg tidak ditemukan")

    t0 = sec(a.t0)
    # durasi video HP
    p = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", a.phone],
        capture_output=True, text=True,
    )
    try:
        dur = float(p.stdout.strip())
    except ValueError:
        sys.exit("ffprobe durasi gagal: " + p.stderr[:200])

    events = load_events(a.log, t0)
    if not events:
        sys.exit("log kosong/tidak ada baris ber-timestamp")

    tmp = tempfile.mkdtemp(prefix="mkvideo_")
    ass = tmp + "/sub.ass"
    build_ass(events, dur, ass)

    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-i", a.phone,
        "-f", "lavfi", "-i", "color=c=0x0e1116:s=1434x1080:d=%.2f" % dur,
        "-filter_complex",
        "[0:v]scale=-2:1080:flags=bicubic,setsar=1[p];"
        "[p][1:v]hstack=inputs=2,setsar=1[base];"
        "[base]ass=%s[v]" % ass,
        "-map", "[v]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
        "-movflags", "+faststart",
        a.out,
    ]
    r = subprocess.run(cmd)
    if r.returncode == 0:
        print("MKVIDEO_OK dur=%.1fs events=%d out=%s" % (dur, len(events), a.out))
    else:
        sys.exit("ffmpeg gagal rc=%d" % r.returncode)


if __name__ == "__main__":
    main()
