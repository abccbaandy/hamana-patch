#!/usr/bin/env python3
"""從 Hamana 安裝目錄建立測試沙箱（見 docs/hamana-internals.md「沙箱測試工法」）。

用法：
    python tools/re/make_sandbox.py <Hamana 安裝目錄> <沙箱目錄> [--exe Hamana.exe.orig] [--set Key=Val ...]
            [--drop-spi ifgdip.spi ...]

- 複製 exe（--exe 指定來源檔名，例如 .orig 原版）、Hamana.key、HamanaLang.dll 與
  Codecs/ DefaultSPIs/ Formats/ BaseTextures/ HamanaShader/。
- Hamana.ini 由安裝目錄的 ini 產生（latin-1 原樣保留位元組）：丟掉 [Recent File List]，覆寫沙箱必要鍵（SAFE），再套 --set。
  --set 的鍵若 ini 沒有，補在 [Settings]。
- --drop-spi 從沙箱 DefaultSPIs/ 移除指定 plugin（用來判定「誰解碼」與回退路徑）。
- 沙箱目錄須不存在或為空，避免誤刪。
"""
import argparse
import io
import os
import re
import shutil
import sys

DIRS = ['Codecs', 'DefaultSPIs', 'Formats', 'BaseTextures', 'HamanaShader']
FILES = ['Hamana.key', 'HamanaLang.dll']
SAFE = {
    'Only1Instance': '0', 'OpenLastImageAuto': '0', 'IsFullscreen': '0',
    'HistoryCount': '0', 'History2Count': '0',
    'Left': '-5000', 'Right': '-3700', 'Top': '-3000', 'Bottom': '-2100',
}


def build_ini(src, sets):
    text = io.open(src, encoding='latin-1').read()
    text = re.sub(r'(?ms)^\[Recent File List\].*?(?=^\[|\Z)', '', text)
    for k, v in sets.items():
        text, n = re.subn(r'(?m)^%s=.*$' % re.escape(k), lambda _: '%s=%s' % (k, v), text)
        if not n:
            text = re.sub(r'(?m)^\[Settings\]\s*$', lambda m: m.group(0) + '\n%s=%s' % (k, v), text, count=1)
    return text


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('src')
    ap.add_argument('dst')
    ap.add_argument('--exe', default='Hamana.exe')
    ap.add_argument('--set', action='append', default=[])
    ap.add_argument('--drop-spi', action='append', default=[])
    a = ap.parse_args()
    if os.path.exists(a.dst) and os.listdir(a.dst):
        sys.exit('沙箱目錄非空：%s' % a.dst)
    os.makedirs(a.dst, exist_ok=True)
    shutil.copy2(os.path.join(a.src, a.exe), os.path.join(a.dst, 'Hamana.exe'))
    for f in FILES:
        shutil.copy2(os.path.join(a.src, f), a.dst)
    for d in DIRS:
        if os.path.isdir(os.path.join(a.src, d)):
            shutil.copytree(os.path.join(a.src, d), os.path.join(a.dst, d))
    for s in a.drop_spi:
        os.remove(os.path.join(a.dst, 'DefaultSPIs', s))
    sets = dict(SAFE)
    sets['ThumbDBDir'] = os.path.join(os.path.abspath(a.dst), 'thumb')
    sets.update(kv.split('=', 1) for kv in a.set)
    io.open(os.path.join(a.dst, 'Hamana.ini'), 'w', encoding='latin-1', newline='').write(
        build_ini(os.path.join(a.src, 'Hamana.ini'), sets))
    print('sandbox ok:', ', '.join(a.set) or '(default)',
          '| drop:', ','.join(a.drop_spi) or '-')


if __name__ == '__main__':
    main()
