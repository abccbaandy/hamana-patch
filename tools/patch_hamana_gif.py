#!/usr/bin/env python3
"""停用 Hamana 內建的 GIF 內容解析（動畫 GIF 偵測），改由 GDI+（ifgdip.spi）靜態解碼。

攻擊面（見 docs/attack-surface.md「內建解析器」GIF 列）：
開圖分派函式 0x45a780 在 jpg / SPI 之前，會對 [Movie] Extensions 內的副檔名呼叫
0x456ca0。該函式對「真正的影片」（.avi/.mpg 等，非 gif/cbz/zip）直接回傳 true
（走影片路徑，不讀檔內容）；只有 gif/cbz/zip 才呼叫 0x451cb0 做內容解析：
  0x451cb0  比對 "GIF"/"89a" magic，GIF89a 時讀 13-byte 螢幕描述元並列舉區塊
  0x451a10  逐一讀取 block（0x21 擴充 / 0x2c 影像 / 0x3b 結尾），數影像 block 判斷是否多幀
  0x451610  讀 13-byte header
全部是自寫、以 VS2005 checked-iterator 讀不可信內容的 2006 年程式碼，
損壞 GIF 越界時與 EXIF（BUG-3）同機制觸發 _invalid_parameter → 0xC000000D
（對應 Hamana.txt「不正なGIFファイルのあるフォルダに移動するとエラー」）。

關鍵事實（CONFIRMED）：動畫顯示閘門旗標 0x68e4dd 在 .data、初值 0、全檔零寫入
→ 執行期恆為 0 → 0x45a8a4 的 `cmp [0x68e4dd],0; je 一般路徑` 永遠成立
→ 0x456ca0 回傳的「是否動畫 GIF」結果**每次都被丟棄**。亦即這段內容解析每開一張
.gif 都跑，卻毫無用途（動畫顯示路徑 0x45a8ad+ 是死碼）。

改法：0x451cb0 的唯一呼叫者是 0x456ca0 內的 0x456d6c，把該 `call 0x451cb0`
（e8 3f af ff ff）換成 `xor eax,eax; nop; nop; nop`（31 c0 90 90 90）。
push 的引數由其後既有的 `add esp,4` 清掉，al=0 → 視為非動畫 GIF → 0x456ca0 回傳 false
→ 分派走一般路徑 → GDI+/ifgdip.spi 靜態解碼。0x451cb0/0x451a10/0x451610 成死碼。

影響：
- 靜態 GIF：不變（本來就是 GDI+ 解像素）。
- 影片（.avi/.mpg…）：不受影響（0x456ca0 對它們不走這個分支）。
- 動畫 GIF 播放偵測：退場——但其顯示路徑本就被 0x68e4dd≡0 關死，使用者端無可見差異。

5 bytes、檔案大小不變、可完整還原。

用法：
    python tools/patch_hamana_gif.py "<Hamana>\\Hamana.exe" [--check | --revert]
"""
import argparse
import os
import shutil
import sys

IMAGE_BASE = 0x400000
VA = 0x456d6c                      # call 0x451cb0 的位址（GIF 內容解析唯一入口）
PRE = '385c2450 7423 57'          # cmp [esp+0x50],bl ; je 0x456d8e ; push edi（被改 call 之前）
ORIG = bytes.fromhex('e83fafffff')   # call 0x451cb0
NEW = bytes.fromhex('31c0909090')    # xor eax,eax ; nop ; nop ; nop
POST = '83c404 837c243408'         # add esp,4 ; cmp [esp+0x34],8（被改 call 之後）


def text_off(va):
    # .text：VA 0x401000 ↔ file 0x1000（Hamana 1.48，sections 1:1 對齊）
    return va - IMAGE_BASE


def state_of(d):
    """回傳 'original' / 'patched' / None（版本不符或被其他工具改過此區）。"""
    o = text_off(VA)
    pre_b = bytes.fromhex(PRE.replace(' ', ''))
    post_b = bytes.fromhex(POST.replace(' ', ''))
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
        print('%s：已套用（內建 GIF 解析退場，改由 GDI+ 靜態解碼）' % path)
    elif st == 'original':
        print('%s：未套用（每開一張 GIF 都跑自寫 GIF 內容解析）' % path)
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
    print('patch 完成（5 bytes，檔案大小不變）：內建 GIF 內容解析退場')


def do_revert(path):
    st = state_of(open(path, 'rb').read())
    if st is None:
        raise SystemExit('簽章不符，無法安全還原；請改用 .orig 備份')
    if st == 'patched':
        d = bytearray(open(path, 'rb').read())
        o = text_off(VA)
        d[o:o + len(ORIG)] = ORIG
        open(path, 'wb').write(bytes(d))
        print('已還原 GIF patch（其他 patch 不受影響）：%s' % path)
    else:
        print('本 patch 未套用，不需還原')


def main():
    ap = argparse.ArgumentParser(description='停用內建 GIF 內容解析，改由 GDI+ 靜態解碼')
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
