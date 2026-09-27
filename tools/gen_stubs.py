#!/usr/bin/env python3
"""Generate JNI stub C source + lib-name list from apktool smali tree.

Run LOCALLY against the decompiled APK (needs base_dec/), then commit the
outputs. On the runner one stub .so is compiled from stubs.c and copied
under every lib name, so all native methods of the app resolve (returning
0/empty values) on an x86_64 emulator where the real arm64 libs cannot run.

Usage:
  python3 gen_stubs.py [SMALI_ROOT] [REAL_APK] [OUT_C] [OUT_NAMES]
"""
import os
import re
import sys
import zipfile
from collections import defaultdict

SMALI_ROOT = sys.argv[1] if len(sys.argv) > 1 else '/home/ubuntu/workspace/btse/base_dec'
APK = sys.argv[2] if len(sys.argv) > 2 else '/home/ubuntu/workspace/btse/signed_out/btse_patched-aligned-debugSigned.apk'
OUT_C = sys.argv[3] if len(sys.argv) > 3 else 'tools/stubs.c'
OUT_NAMES = sys.argv[4] if len(sys.argv) > 4 else 'tools/libnames.txt'

natives = []
lib_calls = []  # (cls, kind, name_or_none)

for dirpath, _dirnames, filenames in os.walk(SMALI_ROOT):
    for fn in filenames:
        if not fn.endswith('.smali'):
            continue
        path = os.path.join(dirpath, fn)
        rel = os.path.relpath(path, SMALI_ROOT)
        parts = rel.split(os.sep)
        if not parts[0].startswith('smali'):
            continue
        cp = parts[1:]
        cp[-1] = cp[-1][:-len('.smali')]
        cls = '/'.join(cp)
        cur = None
        last_str = None
        with open(path, encoding='utf-8', errors='replace') as f:
            for line in f:
                s = line.strip()
                if s.startswith('.method'):
                    toks = s.split()
                    cur = None
                    if 'native' in toks[1:-1]:
                        m = re.match(r'^(.*)\(([^)]*)\)(.*)$', toks[-1])
                        if m:
                            cur = {'cls': cls, 'name': m.group(1),
                                   'args': m.group(2), 'ret': m.group(3),
                                   'kind': 'normal'}
                            natives.append(cur)
                elif s.startswith('.annotation') and cur is not None:
                    if 'FastNative;' in s:
                        cur['kind'] = 'fast'
                    elif 'CriticalNative;' in s:
                        cur['kind'] = 'crit'
                elif s.startswith('.end method'):
                    cur = None

                if s.startswith('const-string'):
                    m = re.match(r'^const-string(?:/jumbo)?\s+\S+,\s*"(.*)"$', s)
                    if m:
                        last_str = m.group(1)
                if 'Ljava/lang/System;->loadLibrary(' in s or \
                   'Ljava/lang/Runtime;->loadLibrary(' in s:
                    lib_calls.append((cls, 'loadLibrary', last_str))
                elif 'Ljava/lang/System;->load(' in s or \
                     'Ljava/lang/Runtime;->load(' in s:
                    lib_calls.append((cls, 'load', last_str))

# ---- lib file names: original arm64 files + every loadLibrary() argument ----
libnames = set()
if os.path.exists(APK):
    with zipfile.ZipFile(APK) as z:
        for n in z.namelist():
            m = re.match(r'^lib/[^/]+/(.+\.so)$', n)
            if m:
                libnames.add(m.group(1))
called = set()
for _cls, kind, name in lib_calls:
    if kind == 'loadLibrary' and name:
        called.add(name if name.startswith('lib') else 'lib' + name + '.so')
libnames |= called

# ---- C emission ----
CTYPE = {'V': 'void', 'Z': 'jboolean', 'B': 'jbyte', 'C': 'jchar',
         'S': 'jshort', 'I': 'jint', 'J': 'jlong', 'F': 'jfloat',
         'D': 'jdouble', 'Ljava/lang/String;': 'jstring'}
ARR_C = {'[B': 'jbyteArray', '[Z': 'jbooleanArray', '[C': 'jcharArray',
         '[S': 'jshortArray', '[I': 'jintArray', '[J': 'jlongArray',
         '[F': 'jfloatArray', '[D': 'jdoubleArray'}
EMPTY_ARR = {'[B': 'NewByteArray', '[Z': 'NewBooleanArray',
             '[C': 'NewCharArray', '[S': 'NewShortArray',
             '[I': 'NewIntArray', '[J': 'NewLongArray',
             '[F': 'NewFloatArray', '[D': 'NewDoubleArray'}


def ctype(ret):
    if ret in CTYPE:
        return CTYPE[ret]
    if ret in ARR_C:
        return ARR_C[ret]
    if ret[:1] in ('L', '['):
        return 'jobject'
    raise SystemExit('bad return type: ' + ret)


def rkind(ret):
    if ret == 'V':
        return 'v'
    if ret in ('F', 'D'):
        return 'x'
    return 'r'


def esc_class(cls):
    out = []
    for ch in cls.replace('/', '.'):
        if ch == '.':
            out.append('_')
        elif ch == '_':
            out.append('_1')
        elif ch == '$':
            out.append('_0024')
        elif ch.isascii() and ch.isalnum():
            out.append(ch)
        else:
            out.append('_0%04X' % ord(ch))
    return ''.join(out)


def esc_method(name):
    out = []
    for ch in name:
        if ch == '_':
            out.append('_1')
        elif ch.isascii() and ch.isalnum():
            out.append(ch)
        else:
            out.append('_0%04X' % ord(ch))
    return ''.join(out)


def esc_sig(args):
    out = []
    for ch in args:
        if ch == '_':
            out.append('_1')
        elif ch == ';':
            out.append('_2')
        elif ch == '[':
            out.append('_3')
        elif ch == '/':
            out.append('_')
        elif ch.isascii() and ch.isalnum():
            out.append(ch)
        else:
            out.append('_0%04X' % ord(ch))
    return ''.join(out)


def body(n):
    ret = n['ret']
    if n['kind'] != 'normal':
        # FastNative/CriticalNative: no JNIEnv available -> never touch env
        return 'return;' if ret == 'V' else 'return 0;'
    if ret == 'Ljava/lang/String;':
        return 'return (*env)->NewStringUTF(env, "");'
    if ret in EMPTY_ARR:
        return 'return (*env)->%s(env, 0);' % EMPTY_ARR[ret]
    return 'return;' if ret == 'V' else 'return 0;'


def emit(sym, n):
    ct = ctype(n['ret'])
    params = 'JNIEnv* env, jobject self' if n['kind'] == 'normal' else 'void'
    return ('JNIEXPORT %s JNICALL\n%s(%s)\n{\n    %s;\n}\n'
            % (ct, sym, params, body(n)))


groups = defaultdict(list)
for n in natives:
    groups[(n['cls'], n['name'])].append(n)

seen = set()
chunks = ['#include <jni.h>', '']


def add(sym, n):
    if sym in seen:
        return
    seen.add(sym)
    chunks.append(emit(sym, n))


n_short = n_long = 0
for (cls, name), ms in sorted(groups.items()):
    uniq = {}
    for m in ms:
        uniq[(m['args'], m['ret'], m['kind'])] = m
    ms = list(uniq.values())
    short = 'Java_%s_%s' % (esc_class(cls), esc_method(name))
    if len(ms) == 1:
        add(short, ms[0])
        n_short += 1
        add(short + '__' + esc_sig(ms[0]['args']), ms[0])
        n_long += 1
    else:
        if len({rkind(m['ret']) for m in ms}) == 1:
            add(short, ms[0])
            n_short += 1
        for m in ms:
            add(short + '__' + esc_sig(m['args']), m)
            n_long += 1

header = [
    '/* Generated by gen_stubs.py from apktool smali -- DO NOT HAND EDIT.',
    ' * %d native methods in %d classes; %d short + %d long JNI symbols.'
    % (len(natives), len(groups), n_short, n_long),
    ' * Normal natives receive (JNIEnv*, jobject); FastNative/CriticalNative',
    ' * take no args (so arg-passing convention mismatches are harmless).',
    ' */',
    '',
]
with open(OUT_C, 'w') as f:
    f.write('\n'.join(header) + '\n' + '\n'.join(chunks))

with open(OUT_NAMES, 'w') as f:
    f.write('# generated by gen_stubs.py\n')
    for n in sorted(libnames):
        f.write(n + '\n')

kinds = defaultdict(int)
for n in natives:
    kinds[n['kind']] += 1
print('natives: %d (normal=%d fast=%d crit=%d) in %d classes'
      % (len(natives), kinds['normal'], kinds['fast'], kinds['crit'],
         len(groups)))
print('symbols: %d short + %d long' % (n_short, n_long))
print('loadLibrary calls: %d, System.load calls: %d'
      % (sum(1 for c in lib_calls if c[1] == 'loadLibrary'),
         sum(1 for c in lib_calls if c[1] == 'load')))
print('called lib names: %s' % sorted(called))
print('total lib files to stub: %d -> %s' % (len(libnames), sorted(libnames)))
for c in lib_calls:
    if c[1] == 'load':
        print('  LOAD(abs): %s last_str=%r' % (c[0], c[2]))
print('wrote %s (%d bytes), %s' % (OUT_C, os.path.getsize(OUT_C), OUT_NAMES))
