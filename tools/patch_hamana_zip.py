#!/usr/bin/env python3
"""停用 Hamana 的內建 zip/cbz 處理，改由 Susie SPI（ax7z.spi + 7z.dll）接手。

背景：Hamana 對副檔名 `.zip` / `.cbz` 的壓縮檔會走自己內建的解壓常式
（minizip 0.15 / 1998 + zlib 1.1.4 / 2002），而非 ax7z.spi。該常式的缺陷
無法藉由更新任何第三方 lib 修復（例：BUG-1，含 0-byte entry 的 zip 會崩潰）。

Hamana 是用一對 UTF-16 字串常數 `"zip"` / `"cbz"` 來判定「這是我自己處理的格式」。
把它們改成不可能出現的副檔名後，Hamana 就找不到內建 handler，
於是把檔案交給 SPI —— 整條 2006 年的解壓程式碼就此退場。

實測效果（見 docs/solutions.md L0）：
  - 含 0-byte entry 的 zip：崩潰 → 正常開啟，空 entry 被自動略過
  - .cbz：崩潰 → 正常（需一併設定 ax7z 的 user_extensions）
  - **加密 zip：開不了 → 彈出 ax7z 密碼對話框**（原為 Hamana 已知限制）
  - 日後 zip 解壓出問題，可直接換 DefaultSPIs/7z.dll 版本處理

前置需求：
  - DefaultSPIs/ax7z.spi 與 DefaultSPIs/7z.dll 存在
  - `.cbz` / `.cbr` 需加進 ax7z 的 user_extensions（7z.dll 的內建清單沒有它們）。
    本工具的 --ax7z-ini 可自動處理。

用法：
    python patch_hamana_zip.py <Hamana.exe 路徑>
    python patch_hamana_zip.py <Hamana.exe 路徑> --ax7z-ini "<ax7z.spi.ini 路徑>"
    python patch_hamana_zip.py <Hamana.exe 路徑> --check
    python patch_hamana_zip.py <Hamana.exe 路徑> --revert

ax7z.spi.ini 預設位置：%APPDATA%\\ax7z\\ax7z.spi.ini
"""
import argparse
import io
import os
import re
import shutil

U16 = 'utf-16-le'
# 這兩個字串在 .rdata 中相鄰，合起來當作定位用的簽章
OLD_CBZ, OLD_ZIP = 'cbz\x00', 'zip\x00'
NEW_CBZ, NEW_ZIP = 'czq\x00', 'zzq\x00'          # 同長度，且不可能是真副檔名

NEEDED_EXTS = ['cbz', 'cbr']


def sig(cbz, zip_):
    return (cbz + zip_).encode(U16)


def locate(d):
    """回傳 (cbz_off, zip_off, state)。state: 'original' / 'patched' / None"""
    for cbz, zp, state in ((OLD_CBZ, OLD_ZIP, 'original'),
                           (NEW_CBZ, NEW_ZIP, 'patched')):
        hits = [m.start() for m in re.finditer(re.escape(sig(cbz, zp)), d)]
        if len(hits) == 1:
            return hits[0], hits[0] + len(cbz) * 2, state
        if len(hits) > 1:
            raise SystemExit('簽章出現 %d 次，無法確定位置' % len(hits))
    return None, None, None


def do_check(path):
    d = open(path, 'rb').read()
    cbz_off, zip_off, state = locate(d)
    if state is None:
        print('%s：無法辨識，可能不是預期的 Hamana 版本' % path)
    elif state == 'patched':
        print('%s：已套用 patch（內建 zip/cbz 已停用，由 SPI 接手）' % path)
    else:
        print('%s：原始狀態（zip/cbz 仍由內建常式處理）' % path)
    if state:
        print('   cbz 字串 offset %d，zip 字串 offset %d' % (cbz_off, zip_off))
    return state


def patch_ax7z_ini(ini_path):
    """把 cbz/cbr 加進 ax7z 的 user_extensions。"""
    if not os.path.isfile(ini_path):
        print('  ax7z ini 不存在，略過：%s' % ini_path)
        print('  （請在 ax7z 設定對話框的 User-defined 手動加入 cbz;cbr）')
        return
    backup = ini_path + '.orig'
    if not os.path.exists(backup):
        shutil.copy(ini_path, backup)
        print('  已備份 → %s' % backup)
    text = io.open(ini_path, encoding='utf-8', errors='replace').read()
    m = re.search(r'(?m)^user_extensions=(.*)$', text)
    if not m:
        print('  找不到 user_extensions，請手動設定')
        return
    exts = [e.strip() for e in m.group(1).split(';') if e.strip()]
    added = [e for e in NEEDED_EXTS if e not in exts]
    if not added:
        print('  ax7z user_extensions 已包含 %s' % '/'.join(NEEDED_EXTS))
        return
    new_line = 'user_extensions=' + ';'.join(exts + added)
    text = text[:m.start()] + new_line + text[m.end():]
    io.open(ini_path, 'w', encoding='utf-8', newline='').write(text)
    print('  ax7z user_extensions 追加：%s' % ';'.join(added))


def do_patch(path, ax7z_ini):
    d = bytearray(open(path, 'rb').read())
    cbz_off, zip_off, state = locate(bytes(d))
    if state == 'patched':
        print('已經是 patch 過的版本，不需重複執行')
        # ax7z.spi.ini 可能被刪除重建過，仍確認一次 cbz/cbr 設定
        patch_ax7z_ini(ax7z_ini or default_ax7z_ini())
        return
    if state is None:
        raise SystemExit('找不到預期的字串簽章；此 Hamana 版本不受支援')

    backup = path + '.orig'
    if not os.path.exists(backup):
        shutil.copy(path, backup)
        print('已備份原檔 → %s' % backup)

    d[cbz_off:cbz_off + 8] = NEW_CBZ.encode(U16)
    d[zip_off:zip_off + 8] = NEW_ZIP.encode(U16)
    open(path, 'wb').write(bytes(d))
    print('patch 完成（檔案大小不變）')
    print('  "cbz" → "czq"  @ %d' % cbz_off)
    print('  "zip" → "zzq"  @ %d' % zip_off)
    print()
    print('設定 ax7z 自訂副檔名：')
    patch_ax7z_ini(ax7z_ini or default_ax7z_ini())
    print()
    print('注意：patch 後 zip 由 ax7z.spi + DefaultSPIs/7z.dll 處理，')
    print('      兩者缺一不可，勿刪除 DefaultSPIs 資料夾。')


def do_revert(path):
    backup = path + '.orig'
    if not os.path.exists(backup):
        raise SystemExit('找不到備份檔 %s' % backup)
    shutil.copy(backup, path)
    print('已還原 %s' % path)
    print('（ax7z 的 user_extensions 不影響原始行為，可保留）')


def default_ax7z_ini():
    appdata = os.environ.get('APPDATA', '')
    return os.path.join(appdata, 'ax7z', 'ax7z.spi.ini')


def main():
    ap = argparse.ArgumentParser(
        description='停用 Hamana 內建 zip/cbz 處理，改由 ax7z.spi 接手')
    ap.add_argument('exe', help='Hamana.exe 路徑')
    ap.add_argument('--ax7z-ini', default=None,
                    help='ax7z.spi.ini 路徑（預設 %%APPDATA%%\\ax7z\\ax7z.spi.ini）')
    g = ap.add_mutually_exclusive_group()
    g.add_argument('--revert', action='store_true', help='由 .orig 備份還原')
    g.add_argument('--check', action='store_true', help='只檢查目前狀態')
    args = ap.parse_args()

    if not os.path.isfile(args.exe):
        raise SystemExit('檔案不存在：%s' % args.exe)
    if args.check:
        do_check(args.exe)
    elif args.revert:
        do_revert(args.exe)
    else:
        do_patch(args.exe, args.ax7z_ini)


if __name__ == '__main__':
    main()
