#!/usr/bin/env python3
"""讓 JPEG 開啟路徑的 libjpeg 檔頭/marker 解析 + 自寫 EXIF 解析完全退場。

背景（見 docs/attack-surface.md「JPEG 退場」殘留攻擊面）：
L2 之後 jpg 像素已改由 ifgdip.spi（GDI+）解碼，但開每張 JPEG 時分派函式 0x45a780
仍會在 SPI 解碼成功後呼叫 0x456fc0 擷取 EXIF（兩處：0x45a9e2、0x45aa9f）。
0x456fc0 內部經 0x452ee0 / 0x452f90 呼叫 libjpeg core 0x452720（唯二呼叫者在
0x452f53 / 0x453006），讀檔頭/marker 後交給自寫 EXIF 解析器
（0x454d90 / 0x453600 / 0x4546c0，BUG-3 的越界來源，已由 L0b 攔截）。

亦即：libjpeg（1998）marker 解析 + 自寫 EXIF 解析，**每開一張 JPEG 都無條件跑**
（與是否開啟 ShowExifMainInfo/SubInfo/GpsInfo 資訊面板無關；面板只顯示已解析好的
結果）。要讓這段碼不跑、徹底移出攻擊面，只能讓 0x456fc0 不執行本體。

關鍵事實（CONFIRMED，反組譯）：
- 0x456fc0 回傳值在兩個呼叫端都被忽略（add esp,8 後直接 mov al,1; jmp），cdecl 2 引數。
- 0x456fc0 自身在 APP1 為空時走 0x457015 的乾淨 epilogue（xor al,al；還原 fs:[0]；
  pop ecx/esi；add esp,0x30；ret），此即「此 JPEG 無 EXIF」的正常回傳路徑，
  callers 對它處理無礙（否則無 EXIF 的 JPEG 根本開不了）。
- Hamana 全檔無 "Orientation" 字串 → 不靠 EXIF 自動轉正圖片 → 退場不影響顯示方向。

改法：在 prologue 之後（esi=arg 已載入）把 0x456fe8 的
  cmp byte [esi+0x264],0        (80 be 64 02 00 00 00, 7 bytes)
換成
  jmp 0x457015 ; nop ; nop      (e9 28 00 00 00 90 90, 7 bytes)
→ 函式立即走自身乾淨 epilogue 回傳 al=0（視同無 EXIF），不碰 libjpeg / 自寫 EXIF。
單一退場點涵蓋兩個呼叫端；0x452ee0/0x452f90/0x452720(對外路徑)/自寫 EXIF 解析成死碼。

影響：
- 正常看圖 / 轉向 / 像素解碼：不變（像素早由 GDI+ 解好）。
- EXIF 資訊面板（ShowExif*Info，預設關閉、須手動 toggle）：顯示空白。
- libjpeg 殘留於對外 JPEG 路徑：清零（ThumbDB 自用路徑不吃外部檔，不在攻擊面上）。

7 bytes、檔案大小不變、可完整還原。

用法：
    python tools/patch_hamana_exif_retire.py "<Hamana>\\Hamana.exe" [--check | --revert]
"""
import argparse
import os
import shutil
import sys

IMAGE_BASE = 0x400000
VA = 0x456fe8                               # cmp byte [esi+0x264],0（prologue 之後的第一條）
PRE = '8b74243c'                            # mov esi,[esp+0x3c]（被改之前）
ORIG = bytes.fromhex('80be6402000000')     # cmp byte [esi+0x264],0
NEW = bytes.fromhex('e928000000 9090'.replace(' ', ''))  # jmp 0x457015 ; nop ; nop
POST = 'c744240c00000000'                   # mov dword [esp+0xc],0（被改之後）


def text_off(va):
    # .text：VA 0x401000 ↔ file 0x1000（Hamana 1.48，sections 1:1 對齊）
    return va - IMAGE_BASE


def state_of(d):
    """回傳 'original' / 'patched' / None（版本不符或被其他工具改過此區）。"""
    o = text_off(VA)
    pre_b = bytes.fromhex(PRE)
    post_b = bytes.fromhex(POST)
    n = len(ORIG)
    if d[o - len(pre_b):o] != pre_b or d[o + n:o + n + len(post_b)] != post_b:
        return None
    cur = d[o:o + n]
    if cur == ORIG:
        return 'original'
    if cur == NEW:
        return 'patched'
    return None


def do_check(path):
    st = state_of(open(path, 'rb').read())
    if st == 'patched':
        print('%s：已套用（libjpeg/自寫 EXIF 解析退場，EXIF 面板顯示空白）' % path)
    elif st == 'original':
        print('%s：未套用（每開一張 JPEG 都跑 libjpeg marker + 自寫 EXIF 解析）' % path)
    else:
        print('%s：無法辨識，可能不是預期的 Hamana 版本（或被其他工具改過此區）' % path)
    return st


def do_patch(path):
    st = state_of(open(path, 'rb').read())
    if st is None:
        raise SystemExit('簽章不符；此 Hamana 版本不受支援')
    if st == 'patched':
        print('Hamana.exe 已是 patch 過的版本')
        return
    backup = path + '.orig'
    if not os.path.exists(backup):
        shutil.copy(path, backup)
        print('已備份原檔 → %s' % backup)
    d = bytearray(open(path, 'rb').read())
    o = text_off(VA)
    d[o:o + len(NEW)] = NEW
    open(path, 'wb').write(bytes(d))
    print('patch 完成（7 bytes，檔案大小不變）：libjpeg/自寫 EXIF 解析退場')


def do_revert(path):
    st = state_of(open(path, 'rb').read())
    if st is None:
        raise SystemExit('簽章不符，無法安全還原；請改用 .orig 備份')
    if st == 'patched':
        d = bytearray(open(path, 'rb').read())
        o = text_off(VA)
        d[o:o + len(ORIG)] = ORIG
        open(path, 'wb').write(bytes(d))
        print('已還原 EXIF 退場 patch（其他 patch 不受影響）：%s' % path)
    else:
        print('本 patch 未套用，不需還原')


def main():
    ap = argparse.ArgumentParser(description='讓 libjpeg/自寫 EXIF 解析在 JPEG 開啟路徑退場')
    ap.add_argument('exe', help='Hamana.exe 路徑')
    g = ap.add_mutually_exclusive_group()
    g.add_argument('--revert', action='store_true', help='還原本 patch 改動的 bytes')
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
