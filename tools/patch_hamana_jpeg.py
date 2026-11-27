#!/usr/bin/env python3
"""讓 Hamana 的 .jpg/.jpeg 改由 GDI+（ifgdip.spi）解碼，內建 libjpeg（IJG 6b，1998）退場。

開圖分派函式 0x45a780 的流程（見 docs/hamana-internals.md「解碼器分派」）：
  2. Respect_libjpeg && isJpeg → 內建 libjpeg
  3. Respect_Gdiplus → 副檔名屬 GDI+ 且「非 jpeg」→ ifgdip.spi
  4. 一般 SPI 迴圈（ifwebp.spi 等）
  5. !Respect_libjpeg && isJpeg → 內建 libjpeg
  6. 第 3 步沒試過 → ifgdip.spi
原版的 jpeg 永遠不會交給 GDI+，最後一定進 libjpeg。需要兩處配合：

1. Hamana.exe：改 3 個條件跳躍（各 1 byte）
     A 0x45a905  je  → jmp  跳過第 2 步
     B 0x45a96e  je  → jmp  jpeg 也算 GDI+ 副檔名（第 3、6 步可用）
     C 0x45aadc  jne → jmp  跳過第 5 步
2. ifgdip.spi 預設不宣告 *.jpg（GetPluginInfo 依 jpg 旗標，預設 0）。旗標從 ini 的 [ifgdip] jpg= 讀，
   但 ifgdip v0.8 組路徑有 bug：GetModuleFileName 後沒去掉檔名就接 "ifgdip.ini"，
   實際讀的是 DefaultSPIs\\ifgdip.spiifgdip.ini（CONFIRMED，32-bit harness 讀其內部字串）。
   所以本工具寫的是這個怪檔名。

結果：jpg → GDI+ → SPI（ifwebp 接手副檔名錯置的 WebP）→ 失敗，不再進 libjpeg 解像素。
不受影響：解碼成功後的 EXIF 讀取（0x456fc0，libjpeg 只讀檔頭，不呼叫 jpeg_start_decompress）、
縮圖快取 HamanaThumb/*.mcf（Hamana 自己編碼的 JPEG）。Respect_libjpeg 失去作用。
沒有 ifgdip.spi 時套用會讓所有 JPEG 開不了，所以本工具會拒絕。

用法：
    python tools/patch_hamana_jpeg.py "<Hamana>\\Hamana.exe" [--check | --revert]
"""
import argparse
import io
import os
import re
import shutil
import sys

IMAGE_BASE = 0x400000
# (VA of patched byte, 前文 hex（被改 byte 之前）, 原 byte, 新 byte, 後文 hex)
SITES = [
    (0x45a905, '803dc8bf67000088 45cf', 0x74, 0xEB, '26'),
    (0x45a96e, '885dcd 740b 807dcf00', 0x74, 0xEB, '05'),
    (0x45aadc, '803dc8bf670000', 0x75, 0xEB, '35'),
]
GDIP_SPI = 'ifgdip.spi'
GDIP_INI = GDIP_SPI + 'ifgdip.ini'      # 見 docstring 第 2 點


def text_off(va):
    # .text：VA 0x401000 ↔ file 0x1000（Hamana 1.48，sections 1:1 對齊）
    return va - IMAGE_BASE


def state_of(d):
    """回傳 'original' / 'patched' / None（版本不符或部分套用）。"""
    st = set()
    for va, pre, orig, new, post in SITES:
        o = text_off(va)
        pre_b, post_b = bytes.fromhex(pre.replace(' ', '')), bytes.fromhex(post)
        if d[o - len(pre_b):o] != pre_b or d[o + 1:o + 1 + len(post_b)] != post_b:
            return None
        st.add('original' if d[o] == orig else 'patched' if d[o] == new else None)
    return st.pop() if len(st) == 1 else None


def spi_dir(exe):
    return os.path.join(os.path.dirname(os.path.abspath(exe)), 'DefaultSPIs')


def gdip_jpg(exe):
    """ifgdip 的 jpg 旗標：True / False / None（ifgdip.spi 不存在）。"""
    if not os.path.isfile(os.path.join(spi_dir(exe), GDIP_SPI)):
        return None
    p = os.path.join(spi_dir(exe), GDIP_INI)
    if not os.path.isfile(p):
        return False
    m = re.search(r'(?im)^\s*jpg\s*=\s*(-?\d+)', io.open(p, encoding='latin-1').read())
    return bool(m and int(m.group(1)))


def set_gdip_jpg(exe, on):
    p = os.path.join(spi_dir(exe), GDIP_INI)
    text = io.open(p, encoding='latin-1').read() if os.path.isfile(p) else ''
    if not on:
        if re.fullmatch(r'\s*\[ifgdip\]\s*jpg=1\s*', text):
            os.remove(p)                                   # 本工具建的，整檔刪除
        elif text:
            io.open(p, 'w', encoding='latin-1', newline='').write(
                re.sub(r'(?im)^(\s*jpg\s*=\s*)-?\d+', r'\g<1>0', text))
        return
    if re.search(r'(?im)^\s*jpg\s*=', text):
        text = re.sub(r'(?im)^(\s*jpg\s*=\s*)-?\d+', r'\g<1>1', text)
    elif re.search(r'(?im)^\[ifgdip\]', text):
        text = re.sub(r'(?im)^\[ifgdip\]\s*$', '[ifgdip]\r\njpg=1', text, count=1)
    else:
        text = text + ('' if not text or text.endswith('\n') else '\r\n') + '[ifgdip]\r\njpg=1\r\n'
    io.open(p, 'w', encoding='latin-1', newline='').write(text)


def do_check(path):
    st = state_of(open(path, 'rb').read())
    g = gdip_jpg(path)
    if st == 'patched':
        print('%s：已套用（jpg 改由 GDI+ 解碼）' % path)
    elif st == 'original':
        print('%s：未套用（jpg 由內建 libjpeg 1998 解碼）' % path)
    else:
        print('%s：無法辨識，可能不是預期的 Hamana 版本（或被其他工具改過此區）' % path)
    print('  ifgdip.spi 宣告 jpg：%s' % {None: '找不到 ifgdip.spi', True: '是', False: '否'}[g]
          + ('  ← exe 已 patch 但 ifgdip 不收 jpg，JPEG 會開不了' if st == 'patched' and not g else ''))
    return st


def do_patch(path):
    st = state_of(open(path, 'rb').read())
    if st is None:
        raise SystemExit('簽章不符；此 Hamana 版本不受支援')
    if gdip_jpg(path) is None:
        raise SystemExit('找不到 DefaultSPIs\\%s；沒有它的話 patch 後所有 JPEG 都開不了，略過' % GDIP_SPI)
    set_gdip_jpg(path, True)
    print('ifgdip 設定 jpg=1 → DefaultSPIs\\%s' % GDIP_INI)
    if st == 'patched':
        print('Hamana.exe 已是 patch 過的版本')
        return
    backup = path + '.orig'
    if not os.path.exists(backup):
        shutil.copy(path, backup)
        print('已備份原檔 → %s' % backup)
    d = bytearray(open(path, 'rb').read())
    for va, _, _, new, _ in SITES:
        d[text_off(va)] = new
    open(path, 'wb').write(bytes(d))
    print('patch 完成（3 bytes，檔案大小不變）：jpg 改由 GDI+ 解碼')


def do_revert(path):
    st = state_of(open(path, 'rb').read())
    if st is None:
        raise SystemExit('簽章不符，無法安全還原；請改用 .orig 備份')
    if st == 'patched':
        d = bytearray(open(path, 'rb').read())
        for va, _, orig, _, _ in SITES:
            d[text_off(va)] = orig
        open(path, 'wb').write(bytes(d))
        print('已還原 JPEG patch（其他 patch 不受影響）：%s' % path)
    else:
        print('本 patch 未套用，不需還原')
    if gdip_jpg(path):
        set_gdip_jpg(path, False)
        print('ifgdip 設定 jpg 已關閉')


def main():
    ap = argparse.ArgumentParser(description='jpg 改由 GDI+ 解碼，內建 libjpeg 退場')
    ap.add_argument('exe', help='Hamana.exe 路徑')
    g = ap.add_mutually_exclusive_group()
    g.add_argument('--revert', action='store_true', help='還原本 patch 改動的 bytes 與 ifgdip 設定')
    g.add_argument('--check', action='store_true', help='只檢查目前狀態')
    args = ap.parse_args()
    if not os.path.isfile(args.exe):
        raise SystemExit('檔案不存在：%s' % args.exe)
    if args.check:
        do_check(args.exe)
    elif args.revert:
        do_revert(args.exe)
    else:
        do_patch(args.exe)


if __name__ == '__main__':
    sys.exit(main())
