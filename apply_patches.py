#!/usr/bin/env python3
"""Hamana 修正補丁 —— 一鍵套用 / 還原。

對指定的 Hamana 安裝目錄套用三個二進位補丁與一項設定變更：

  1. Hamana.exe          停用內建 zip/cbz 解壓，改由 ax7z.spi + 7z.dll 接手
  2. Hamana.exe          EXIF 越界防護：壞 EXIF 的 JPEG 不再閃退（0xC000000D）
  2b. Hamana.exe         開啟 DEP（PE header NX_COMPAT bit；見 docs/attack-surface.md）
  2c. Hamana.exe         jpg 改由 GDI+（ifgdip.spi）解碼，內建 libjpeg 1998 退場（3 bytes）
                         + DefaultSPIs/ifgdip.spiifgdip.ini 設 jpg=1（ifgdip 的 ini 路徑 bug，見工具 docstring）
  3. DefaultSPIs/ifwebp.spi  讓它同時宣告 *.jpg / *.jpeg，修正副檔名錯置的 WebP
  4. Hamana.ini          Respect_libjpeg=0（第 3 項的生效前提）
  5. ax7z.spi.ini        user_extensions 追加 cbz;cbr（第 1 項的配套）

1、3 只改字串常數；2 改 85 bytes 程式碼（hook + .text 填充區內的 stub）；2b 改 1 bit。
檔案大小皆不變，且可完整還原。
原理與實測資料見 docs/solutions.md。

用法：
    python apply_patches.py "C:\\path\\to\\Hamana"
    python apply_patches.py "C:\\path\\to\\Hamana" --check
    python apply_patches.py "C:\\path\\to\\Hamana" --revert

本工具不散布 Hamana 本體，請自備。
"""
import argparse
import io
import os
import re
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tools'))

import patch_hamana_zip as zip_patch          # noqa: E402
import patch_hamana_exif as exif_patch        # noqa: E402
import patch_hamana_nx as nx_patch            # noqa: E402
import patch_hamana_jpeg as jpeg_patch        # noqa: E402
import patch_ifwebp as webp_patch             # noqa: E402


def find_ini(hamana_dir):
    p = os.path.join(hamana_dir, 'Hamana.ini')
    return p if os.path.isfile(p) else None


def set_respect_libjpeg(ini_path, value):
    """回傳 True 表示有變更。"""
    text = io.open(ini_path, encoding='utf-8', errors='replace').read()
    m = re.search(r'(?m)^Respect_libjpeg=(\d+)\s*$', text)
    if not m:
        # 沒有這個鍵就補在 [Settings] 底下
        m2 = re.search(r'(?m)^\[Settings\]\s*$', text)
        if not m2:
            print('  ! Hamana.ini 沒有 [Settings] 區段，請手動加入 '
                  'Respect_libjpeg=%s' % value)
            return False
        insert = m2.end()
        text = text[:insert] + '\nRespect_libjpeg=%s' % value + text[insert:]
        io.open(ini_path, 'w', encoding='utf-8', newline='').write(text)
        print('  Hamana.ini 追加 Respect_libjpeg=%s' % value)
        return True
    if m.group(1) == str(value):
        print('  Hamana.ini 已是 Respect_libjpeg=%s' % value)
        return False
    text = text[:m.start()] + 'Respect_libjpeg=%s' % value + text[m.end():]
    io.open(ini_path, 'w', encoding='utf-8', newline='').write(text)
    print('  Hamana.ini Respect_libjpeg=%s → %s' % (m.group(1), value))
    return True


def resolve(hamana_dir):
    exe = os.path.join(hamana_dir, 'Hamana.exe')
    spi = os.path.join(hamana_dir, 'DefaultSPIs', 'ifwebp.spi')
    if not os.path.isfile(exe):
        raise SystemExit('找不到 Hamana.exe：%s\n請指定 Hamana 的安裝目錄。' % exe)
    return exe, (spi if os.path.isfile(spi) else None)


def check_7z_dll(hamana_dir):
    """zip 補丁的前提：DefaultSPIs/7z.dll 存在且為 32-bit。回傳問題描述，正常則 None。"""
    p = os.path.join(hamana_dir, 'DefaultSPIs', '7z.dll')
    if not os.path.isfile(p):
        return '找不到 DefaultSPIs\\7z.dll'
    with open(p, 'rb') as fh:
        d = fh.read(0x400)
    try:
        e = struct.unpack_from('<I', d, 0x3c)[0]
        machine = struct.unpack_from('<H', d, e + 4)[0]
    except struct.error:
        return 'DefaultSPIs\\7z.dll 不是有效的 DLL'
    if machine != 0x14c:
        return 'DefaultSPIs\\7z.dll 不是 32-bit（machine 0x%x），Hamana 載入不了' % machine
    return None


def do_check(hamana_dir):
    exe, spi = resolve(hamana_dir)
    print('[1/4] Hamana.exe（zip/cbz）')
    zip_patch.do_check(exe)
    print()
    print('[2/4] Hamana.exe（EXIF）')
    exif_patch.do_check(exe)
    print('  DEP（NX_COMPAT）：%s' % ('ON' if nx_patch.status(exe) else 'OFF ← 建議開啟'))
    print('  JPEG 解碼器：', end='')
    jpeg_patch.do_check(exe)
    print()
    print('[3/4] DefaultSPIs/ifwebp.spi')
    if spi:
        webp_patch.do_check(spi)
    else:
        print('  未安裝，略過（WebP 支援需要它）')
    print()
    print('[4/4] Hamana.ini')
    ini = find_ini(hamana_dir)
    if not ini:
        print('  尚未產生（Hamana 至少執行過一次才會建立）')
    else:
        m = re.search(r'(?m)^Respect_libjpeg=(\d+)\s*$',
                      io.open(ini, encoding='utf-8', errors='replace').read())
        print('  Respect_libjpeg=%s %s'
              % (m.group(1) if m else '(未設定)',
                 '' if m and m.group(1) == '0' else '← 需為 0'))


def do_apply(hamana_dir, ax7z_ini):
    exe, spi = resolve(hamana_dir)
    print('[1/5] 停用內建 zip/cbz 解壓')
    problem = check_7z_dll(hamana_dir)
    if problem:
        print('  %s，略過（否則 zip 會完全開不了）。' % problem)
        print('  請先依 README「安裝第三方元件」放入 32-bit 7z.dll 與新版 ax7z.spi 再重跑。')
    else:
        zip_patch.do_patch(exe, ax7z_ini)
    print()
    print('[2/5] EXIF 越界防護')
    exif_patch.do_patch(exe)
    print()
    print('[2b/5] 開啟 DEP')
    print('  NX_COMPAT %s' % ('ON' if nx_patch.apply(exe) else '已是 ON'))
    print()
    print('[2c/5] jpg 改由 GDI+ 解碼')
    try:
        jpeg_patch.do_patch(exe)
    except SystemExit as e:
        print('  %s' % e)
    print()
    print('[3/5] 修正 WebP 副檔名分派')
    if spi:
        webp_patch.do_patch(spi)
    else:
        print('  找不到 DefaultSPIs/ifwebp.spi，略過。')
        print('  （需要 WebP 支援的話，請先安裝 ifwebp.spi）')
    print()
    print('[4/5] Hamana.ini 設定')
    ini = find_ini(hamana_dir)
    if not ini:
        print('  Hamana.ini 尚未產生。請先執行一次 Hamana 再跑本工具，')
        print('  或手動在 [Settings] 加入 Respect_libjpeg=0')
    else:
        set_respect_libjpeg(ini, 0)
    print()
    print('[5/5] 完成')
    print('  備份：Hamana.exe.orig / ifwebp.spi.orig / ax7z.spi.ini.orig')
    print('  還原：python apply_patches.py "<目錄>" --revert')


def do_revert(hamana_dir):
    exe, spi = resolve(hamana_dir)
    print('[1/3] Hamana.exe')
    if nx_patch.apply(exe, enable=False):
        print('  已關閉 DEP（NX_COMPAT）')
    try:
        jpeg_patch.do_revert(exe)
    except SystemExit as e:
        print('  %s' % e)
    try:
        exif_patch.do_revert(exe)
    except SystemExit as e:
        print('  %s' % e)
    try:
        zip_patch.do_revert(exe)
    except SystemExit as e:
        print('  %s' % e)
    print()
    print('[2/3] DefaultSPIs/ifwebp.spi')
    if spi:
        try:
            webp_patch.do_revert(spi)
        except SystemExit as e:
            print('  %s' % e)
    else:
        print('  未安裝，略過')
    print()
    print('[3/3] Hamana.ini')
    ini = find_ini(hamana_dir)
    if ini:
        set_respect_libjpeg(ini, 1)
    else:
        print('  Hamana.ini 不存在，略過')


def main():
    ap = argparse.ArgumentParser(
        description='Hamana 修正補丁：一鍵套用 / 還原')
    ap.add_argument('hamana_dir', help='Hamana 安裝目錄（含 Hamana.exe）')
    ap.add_argument('--ax7z-ini', default=None,
                    help='ax7z.spi.ini 路徑（預設 %%APPDATA%%\\ax7z\\ax7z.spi.ini）')
    g = ap.add_mutually_exclusive_group()
    g.add_argument('--check', action='store_true', help='只檢查目前狀態')
    g.add_argument('--revert', action='store_true', help='還原所有變更')
    args = ap.parse_args()

    d = args.hamana_dir.rstrip('"')
    if not os.path.isdir(d):
        raise SystemExit('目錄不存在：%s' % d)

    if args.check:
        do_check(d)
    elif args.revert:
        do_revert(d)
    else:
        do_apply(d, args.ax7z_ini)


if __name__ == '__main__':
    main()
