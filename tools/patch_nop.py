#!/usr/bin/env python3
"""NOP all System.loadLibrary invoke-static sites in BTSE APK dexes + strip arm libs."""
import zipfile, struct, hashlib, zlib, sys, os

SRC = sys.argv[1] if len(sys.argv) > 1 else '/tmp/apks/real.apk'
DST = sys.argv[2] if len(sys.argv) > 2 else '/tmp/apks/nop.apk'
EXPECTED = {'classes.dex':3,'classes2.dex':1,'classes5.dex':1,
            'classes6.dex':5,'classes7.dex':4,'classes9.dex':1}

def uleb(b, o):
    r = 0; s = 0
    while True:
        c = b[o]; o += 1
        r |= (c & 0x7f) << s; s += 7
        if not c & 0x80: break
    return r, o

def find_idx(dex):
    str_off = struct.unpack_from('<I', dex, 60)[0]
    type_off = struct.unpack_from('<I', dex, 68)[0]
    mth_sz, mth_off = struct.unpack_from('<II', dex, 88)
    cache = {}
    def s(i):
        if i in cache: return cache[i]
        so = struct.unpack_from('<I', dex, str_off + i * 4)[0]
        _, p = uleb(dex, so)
        v = dex[p:dex.index(b'\x00', p)].decode('utf-8', 'replace')
        cache[i] = v
        return v
    for i in range(mth_sz):
        cls, proto, name = struct.unpack_from('<HHI', dex, mth_off + i * 8)
        if s(name) == 'loadLibrary':
            dsc = struct.unpack_from('<I', dex, type_off + cls * 4)[0]
            if s(dsc) == 'Ljava/lang/System;':
                return i
    return None

z = zipfile.ZipFile(SRC)
patched = {}
for name, exp in EXPECTED.items():
    dex = bytearray(z.read(name))
    idx = find_idx(bytes(dex))
    if idx is None:
        print('ABORT: no loadLibrary method_id in', name); sys.exit(1)
    lo, hi = idx & 0xff, idx >> 8
    hits = []
    st = 0
    while True:
        p = dex.find(b'\x71', st)
        if p < 0: break
        if p % 2 == 0 and p + 6 <= len(dex) and dex[p+1] == 0x10 and \
           dex[p+2] == lo and dex[p+3] == hi and dex[p+4] <= 0x0f and dex[p+5] == 0x00:
            hits.append(p)
        st = p + 1
    if len(hits) != exp:
        print('ABORT: %s hits=%d expected=%d' % (name, len(hits), exp)); sys.exit(1)
    for p in hits:
        dex[p:p+6] = b'\x00' * 6
    dex[12:32] = hashlib.sha1(bytes(dex[32:])).digest()
    dex[8:12] = struct.pack('<I', zlib.adler32(bytes(dex[12:])) & 0xffffffff)
    patched[name] = bytes(dex)
    print('%s: patched %d' % (name, len(hits)))

zin = zipfile.ZipFile(SRC)
zout = zipfile.ZipFile(DST, 'w', zipfile.ZIP_DEFLATED)
n_lib = 0
for item in zin.infolist():
    if item.filename.startswith('lib/'):
        n_lib += 1
        continue
    data = patched.get(item.filename) or zin.read(item.filename)
    zi = zipfile.ZipInfo(item.filename, date_time=item.date_time)
    zi.compress_type = item.compress_type
    zi.external_attr = item.external_attr
    zout.writestr(zi, data)
zout.close()
print('lib removed: %d, out: %d bytes' % (n_lib, os.path.getsize(DST)))
print('NOP_OK')
