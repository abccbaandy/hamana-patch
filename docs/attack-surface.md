# 攻擊面盤點（惡意檔案 → 記憶體破壞）

目的：決定哪些內建解析器要 patch 退場、改交外部 DLL。威脅模型 = 使用者開啟不可信的圖片 / 壓縮檔 / 資料夾。
工具：`tools/re/pe_audit.py`（安全旗標 + 內嵌函式庫指紋；`--root` 遮蔽安裝路徑）；分派順序與沙箱/除錯工具見 `hamana-internals.md`。
狀態旗標：CONFIRMED / guess / unknown。

## 緩解機制（CONFIRMED，`pe_audit.py` 對原版 exe）

| 模組 | ASLR | DEP 旗標 | SafeSEH | /GS | 備註 |
|---|---|---|---|---|---|
| `Hamana.exe` | n，**無 .reloc**（事後無法開 ASLR） | n | 無 LoadConfig → 無 | 有（`__security_check_cookie` 樣式） | base `0x400000` |
| `HamanaLang.dll` | n | n | - | n | 純資源 |
| `DefaultSPIs/7z.dll`（2024） | Y | Y | - | n | |
| `DefaultSPIs/ax7z.spi`（2015） | Y | Y | Y | Y | |
| `DefaultSPIs/ifwebp.spi`（2024） | Y | Y | - | n | |
| `DefaultSPIs/ifgdip.spi`（2005） | n（有 reloc） | n | Y | Y | 解碼交給系統 GDI+ |
| `Formats/*`、`Codecs/*`（7-zip 4.x，2006） | n | n | - | n | 見下「疑似未使用」 |

- DEP 由 exe 決定。系統政策 `DataExecutionPrevention_SupportPolicy=2`（OptIn），原版行程 `GetProcessMitigationPolicy(ProcessDEPPolicy)` = `0x0` → **原版沒有 DEP**（CONFIRMED）。
- **已修（CONFIRMED）**：`tools/patch_hamana_nx.py` 設 `NX_COMPAT`（DllCharacteristics bit 0x100，file 只差 1 byte），已併入 `apply_patches.py`。patch 後 DEP flags = `0x3`（Enable + DisableAtlThunkEmulation）、permanent=1。
  - 回歸（沙箱，原版 vs patch）：jpg、png24、png32、gif、bmp、webp、壞 EXIF jpg、zip、cbz、zip 改名 .7z、7 種格式混放資料夾（背景預讀）全部 ALIVE，標題 `[WxHxbpp]` 一致。
  - 未涵蓋：設定對話框、投影片、縮圖模式、library、Ghostscript、影片（ATL thunk 若在這些路徑會以 `0xC0000005` 閃退 → 用 `--revert` 還原並記錄）。
  - 已知、與 DEP 無關：ffmpeg 產生的 `.tif` 兩版都讀不出（標題無 `[WxH]`）。
- 強制 ASLR 做不到（exe 沒有 reloc）。另一個做法是 per-app 的惡意探索保護，需要系統管理員權限，未採用。

## 內建解析器（exe 內，不經 SPI）

| 格式 | 實作 | 版本 / 證據 | 開關 | 本機目前 | 風險 |
|---|---|---|---|---|---|
| JPEG | IJG libjpeg + x86 SIMD 1.0 | 1994-1998 版權字串；`Hamana.txt:83` | `Respect_libjpeg` 只改先後，無法關閉 → **已 patch 退場**（`tools/patch_hamana_jpeg.py`） | — | 原版高（所有 jpg 必進 libjpeg）。patch 後像素改由 GDI+ 解；libjpeg 殘留見下「JPEG 退場」 |
| EXIF | 自寫，3 個函式 | `bugs.md` BUG-3 | 無；`ShowExif*Info` 只管顯示 | 每張 JPEG 都會解析 | 中（已知會越界讀取，已由 L0b 攔截） |
| PNG | **無可達的內建解碼器**（CONFIRMED） | 兩組 PNG 字串都是 D3DX 孤兒資料（libpng 1.0.5 reader / writer，無 xref）；開 PNG 時 inflate 不命中；移除 `ifgdip.spi` 後任何設定都讀不出 PNG | `Respect_internalPNGDec` 只決定是否先問 GDI+ | 1 | 無（PNG 一律 GDI+）；不必改設定 |
| zip / cbz | minizip 0.15 + zlib 1.1.4 | `hamana-internals.md` | L0 patch | 已退場 | 已消除 |
| GIF | 自寫「動畫 GIF 偵測器」（`0x456ca0`→`0x451cb0`→`0x451a10`/`0x451610`，VS2005 checked-iterator 讀檔頭+列舉 block）。**非像素解碼器**，像素一律 GDI+ | 無（`[Movie] Extensions` 含 `.gif` 時觸發；顯示閘門 `[0x68e4dd]` 恆 0） | 已 patch 退場（`tools/patch_hamana_gif.py`） | — | 原版中（每開 GIF 都解析不可信內容，與 EXIF 同 `0xC000000D` 機制）。退場後無（見下「GIF 退場」） |
| D3DX | 靜態連結（shader 編譯器、HDR writer 字串） | **不載入使用者檔案（CONFIRMED）**：全檔無 `D3DXCreateTextureFromFile*` 字串，紋理來源字串只有 `BaseTextures`（安裝目錄自帶素材）；PNG 字串為孤兒資料（見 PNG 列） | — | — | 無（不經使用者檔案） |
| `.lnk` | **shell32（非自寫，CONFIRMED）** | `0x4962ef` `CoCreateInstance(CLSID_ShellLink, IID_IShellLinkW)` → QI `IPersistFile`（`0x496311`）→ `Load`（vtable `[+0x14]`，`0x4964bc`）→ `GetPath`（`[+0xc]`，`0x496551`）。無 `Resolve`（不觸發網路/UI）、路徑上無 `ShellExecute` | — | — | **無**：二進位解析交給 Windows shell32（隨 Update 更新）；瀏覽資料夾自動解析每個 .lnk 但只把 target 加進清單、**不執行**（實測，見下「.lnk 退場確認」） |
| library 檔 | 自寫 | `Hamana.txt:253`；`library\` 字串 xref `0x40aba0` | — | — | 低（**非自動可達**）：library 目錄固定為安裝目錄 `library\`（`0x40aba0` 把 `library\` 接到設定 base dir），使用者自管並顯式開啟；瀏覽不可信資料夾/壓縮檔不會自動解析 library 檔 |

## 外部解析器（經 SPI 或系統 DLL）

| 格式 | 元件 | 狀態 |
|---|---|---|
| bmp/jpg/png/gif/tif/ico/wmf/emf | `ifgdip.spi` → 系統 GDI+ | GDI+ 隨 Windows Update 更新；`Respect_Gdiplus=1` |
| webp（+ L2 patch 後的 jpg） | `ifwebp.spi` 2024 | 新 |
| 7z/rar/lzh/arj/cab/zip（L0 後） | `ax7z.spi` 2015 + `DefaultSPIs/7z.dll` 2024 | 新 |
| pdf/ai/eps/ps | Ghostscript `gsdll32.dll`（`GSDllPath`） | 未安裝、`GSDllPath` 空 → 未啟用 |
| 影片（`[Movie] Extensions`，含 `.gif`） | DirectShow + 系統 codec | `EnablePlayMovie=0` → 關閉 |

## 未使用的元件（CONFIRMED）

- `Formats/`、`Codecs/`（7-zip 4.x，2006）從未載入：`minidbg.py --loads` 開真 7z、zip 改名 rar/cbr/lzh/arj/cab，只載入 `DefaultSPIs/7z.dll`。不在攻擊面上，刪不刪都一樣；`ax7z.spi_old`、`ifwebp.spi_` 也不會被載入（副檔名不符）。

## DLL 搜尋路徑（CONFIRMED：開檔不會從圖片資料夾載入 DLL）

- 所有 `LoadLibrary*` 呼叫點（`0x4075e6` 起，共 13 處）：
  - `HamanaLang.dll`：相對名稱，但 exe 目錄優先 → 安全
  - migemo：`GetModuleFileNameW` 取 exe 目錄 + `tmigemo\migemo.dll`（`0x4b41f8`→`0x4b3af2`），絕對路徑
  - Ghostscript：`GSDllPath` 原值（`[0x67d918]`，`0x4a0d7e`）；預設空 → 不載入。使用者若只填檔名就會走搜尋路徑
  - SPI：完整路徑；`7z.dll` 由 ax7z 從自身目錄載入
  - `mfcm80u.dll` 只用 `GetModuleHandle`；`unicows.dll`、`d3d9.dll` 走系統目錄
- 實測：圖片資料夾預放 `tmigemo\migemo.dll`、`gsdll32.dll`、`d3d9d.dll`、`mfcm80u.dll`、`unicows.dll`、`mscoree.dll`、`HamanaLang.dll`、`7z.dll`（誘餌 = 純資源 DLL），開 png/pdf/ps/7z，工作目錄設為 exe 目錄或圖片資料夾 → 都沒有載入。
- 正控制組：刪掉安裝目錄的 `HamanaLang.dll` 且工作目錄 = 圖片資料夾 → 會載入 `<target>\hamanalang.dll`。**安裝目錄缺檔時會被劫持**，不要刪 `HamanaLang.dll`。
- 未涵蓋：migemo 搜尋功能實際觸發時（路徑已靜態確認為絕對路徑）。

## JPEG 退場（CONFIRMED，`tools/patch_hamana_jpeg.py`，已併入 `apply_patches.py` 第 2c 步）

- 改法：分派函式 3 個條件跳躍（`0x45a905`、`0x45a96e`、`0x45aadc`）→ jmp；加上 `DefaultSPIs/ifgdip.spiifgdip.ini` 的 `[ifgdip] jpg=1`（ifgdip 預設不宣告 jpg，且 ini 路徑有 bug，見 `hamana-internals.md`）。兩者缺一 jpg 就開不了。
- libjpeg 的所有入口（`jpeg_CreateDecompress` 只有 `0x452848` 一處，核心 `0x452720`）：

| 呼叫者 | 用途 | patch 後 |
|---|---|---|
| `0x45a450`（分派第 2、5 步） | 解像素 | 不再到達 |
| `0x456fc0`（SPI 解 jpg 成功後） | `jpeg_read_header` + `save_markers(APP1)` → 自寫 EXIF 解析 | 仍會跑，只讀檔頭（`[ebp+0x70]!=0` 直接返回，`jpeg_start_decompress` `0x4bc850` 不命中） |
| `0x4659e0`（另一執行緒） | 解 ThumbDB（`HamanaThumb\*.mcf`，Hamana 自己用 `0x452c80` 編碼的） | 仍會跑；輸入非外部檔（guess：DB 只由 `0x465de0` 寫入） |

- 殘留攻擊面：libjpeg marker 解析（jdmarker）+ 自寫 EXIF 解析（已有 L0b 越界防護）。要完全去掉需讓 `0x456fc0` 不跑（失去 EXIF 顯示），未做。
- 回歸（沙箱，原版 vs patch）：4:2:0/4:2:2/4:4:4、漸進式、灰階、restart marker、截斷、1x1、4001x3001、`.jpeg`、`.JPG`、7z 內 jpg、WebP 改名 `.jpg`、EXIF fuzz 19 種（逐檔 + 同資料夾預讀）、PNG 8 種、壓縮檔 8 種 → 全部 ALIVE、尺寸一致。CMYK JPEG：原版讀不出，patch 後正常；BMP/PNG 改名 `.jpg` 也變得讀得出。`apply_patches --revert` 後 exe/ifwebp 與原檔 hash 相同。
- 未測：GDI+ 對大量圖的解碼速度；EXIF 資訊視窗、投影片、縮圖模式。

## GIF 退場（CONFIRMED，`tools/patch_hamana_gif.py`，已併入 `apply_patches.py` 第 2d 步）

- 攻擊面：分派函式 `0x45a780` 在 jpg/SPI 之前，對 `[Movie] Extensions` 內的副檔名呼叫 `0x456ca0`。該函式對**真正的影片**（.avi/.mpg…，非 gif/cbz/zip）直接回傳 true（走影片路徑，不讀內容）；只有 gif/cbz/zip 才呼叫 `0x451cb0`：比對 `GIF`/`89a` magic、讀 13-byte 螢幕描述元（`0x451610`）、逐一列舉 block（`0x451a10`，`0x21`/`0x2c`/`0x3b`，數影像 block 判斷多幀）。全是自寫、VS2005 checked-iterator 讀不可信內容，損壞 GIF 越界時與 EXIF（BUG-3）同機制觸發 `_invalid_parameter`→`0xC000000D`（對應 `Hamana.txt`「不正なGIFファイルのあるフォルダに移動するとエラー」）。
- **關鍵（CONFIRMED）**：動畫顯示閘門 `[0x68e4dd]` 在 `.data`、初值 0、全檔零寫入（只有 `0x456ee4`、`0x45a8a6` 兩處讀取）→ 執行期恆 0 → `0x45a8a4` 的 `cmp [0x68e4dd],0; je 一般路徑` 永遠成立 → `0x456ca0` 回傳的「是否動畫」結果**每次都被丟棄**，動畫顯示路徑（`0x45a8ad`+）是死碼。亦即這段內容解析每開一張 GIF 都跑，卻毫無用途。
- 改法：`0x451cb0` 的唯一呼叫者是 `0x456d6c`（在 `0x456ca0` 內），把 `call 0x451cb0`（`e8 3f af ff ff`，file offset `0x56d6c`）→ `xor eax,eax; nop×3`（`31 c0 90 90 90`）。引數由其後既有 `add esp,4` 清掉，al=0 → 視為非動畫 → 走一般路徑 → GDI+/ifgdip.spi 靜態解碼。`0x451cb0`/`0x451a10`/`0x451610` 成死碼。5 bytes、大小不變、可還原。
- 回歸（沙箱，原版 vs patch）：靜態/g87/多幀動畫/截斷×2/竄改 block 長度×2（各獨立資料夾）→ 全部 ALIVE、標題 `[WxHxbpp]` 完全一致（靜態 8bpp、動畫 32bpp），兩版皆不 crash。minidbg `--bp 0x451cb0`：原版開 anim.gif 命中（返回位址 `0x456d71`，呼叫鏈 `0x456e06`→`0x4103d0`），patch 版 6s 全程未命中 → 證明移除的是真實執行路徑，非死碼。
- 影響：靜態 GIF 不變（本就 GDI+ 解像素）；影片不受影響（`0x456ca0` 對它們不走此分支）；動畫 GIF 播放偵測退場——但其顯示路徑本就被 `[0x68e4dd]`≡0 關死，使用者端無可見差異。
- 未測：GDI+ 對 GIF 的速度；大量 GIF 資料夾預讀。

## .lnk 退場確認（CONFIRMED，純實測 + 反組譯，無 patch）

`.lnk` 不是 Hamana 自寫 parser，不必 patch。證據：

- 反組譯（`0x496290`–`0x496660`）：對副檔名 `lnk` 的檔案走 `CoCreateInstance(ShellLink)` → `IPersistFile::Load(<lnk 路徑>)` → `IShellLinkW::GetPath`，二進位 `.lnk` 解析 100% 由 `shell32.dll` 完成（隨 Windows Update 更新）。不呼叫 `Resolve`（無 `SLR_*` → 不連網、不跳 UI），解析路徑上無 `ShellExecuteW`（其 5 個 call-site `0x40170d`/`0x40175d`/`0x405489`/`0x436392`/`0x4363a9` 為使用者主動「外部開啟」指令，非瀏覽觸發）。
- 沙箱實測（原版 vs patch，`hidden_run.py`，每樣本獨立資料夾）：
  - 正常 `.lnk`（指向圖）→ ALIVE，target 作為第 2 張加入清單（`(1/2)`）
  - 指向 `calc.exe` 的 `.lnk` → ALIVE，**不加入可視清單**（`(1/1)`）
  - **指向 `.bat` 的 `.lnk`，直接開啟它** → ALIVE，sentinel marker 檔**從未生成** → **不自動執行 target**（CONFIRMED）
  - 截斷/垃圾 `.lnk`（magic 正確但本體截斷、magic 全錯）→ 皆 ALIVE、不崩（shell32 robust）

## 建議順序

1. ~~DEP~~ 已完成。
2. ~~PNG~~ 不需處理：沒有內建 PNG 解碼器可達。
3. ~~Formats/Codecs~~ 從未載入，不在攻擊面上。
4. ~~DLL 搜尋路徑~~ 開檔不會被劫持。
5. ~~JPEG~~ 已退場（見上）。
6. ~~GIF~~ 已退場（見上）。
7. ~~`.lnk`~~ 不在攻擊面上：shell32 解析、不自動執行（見上「.lnk 退場確認」）。
8. ~~D3DX~~ 不在攻擊面上：不載入使用者檔案（無 `D3DXCreateTextureFromFile*`）。
9. ~~library 檔~~ 非自動可達：固定 `library\`、使用者自管。

剩餘唯一在攻擊面上的 Hamana 自寫／內嵌 parser：**開 JPEG 時 `0x456fc0` 的 libjpeg 檔頭/marker 解析（jdmarker，1998）+ 自寫 EXIF 解析**。
- 自寫 EXIF 已有 L0b 越界防護（BUG-3，CONFIRMED）；libjpeg jdmarker 本身僅做長度式 marker 略過，歷史 CVE 多在解壓（IDCT/upsample/color）而非 marker 讀取，風險較低、未另防護。
- 要完全退場需讓 `0x456fc0` 不跑（3 個條件跳躍可改，類似 JPEG patch 作法）→ **代價：EXIF 資訊視窗失去內容**。屬「安全 vs 功能」取捨，待使用者決定是否採用（預設保留 EXIF 顯示，維持現狀 = 已 L0b 緩解）。
