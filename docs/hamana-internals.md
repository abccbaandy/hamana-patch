# Hamana 內部構造與測試工法

供後續 session 快速進入狀況。bug 本身見 `docs/bugs.md`；安全緩解與攻擊面見 `docs/attack-surface.md`。

## 二進位事實（CONFIRMED）

| 項目 | 值 | 取得方式 |
|---|---|---|
| `Hamana.exe` machine | `0x14c`（x86 32-bit） | PE header |
| LARGE_ADDRESS_AWARE | **否**（characteristics `0x103`） | PE characteristics bit 0x20 |
| 內建 JPEG 解碼 | libjpeg（`Copyright 1994-1998, Thomas G. Lane`） | exe 內字串 |
| 內建 zlib | **1.1.4**（inflate/deflate 1995-2002） | exe 內字串 |
| 渲染後端 | Direct3D 9（`hamana_d3d.log` 記錄 adapter / format 能力） | 執行產生 |
| Susie SPI 匯出 | `GetPluginInfo` / `IsSupported` / `GetPicture(Rev)` / `*W` 變體 | exe 內字串 |

## 解碼器分派

**依副檔名，不依檔案內容。** `Respect_libjpeg=0` 不會改成依內容判斷（實測）。

| 設定鍵 | exe 內說明字串 |
|---|---|
| `Respect_libjpeg` | `JPG display gives priority to a built-in decoder.` |
| `Respect_Ghostscript` | `PDF/AI/EPS/PS display use a built-in decoder(Ghostscript).` |
| `Respect_internalPNGDec` / `Respect_Gdiplus` | 同類優先序開關 |

ini 載入（`0x4080e2` 起）→ 全域旗標：`Respect_libjpeg`→`[0x67bfc8]`、`Respect_internalPNGDec`→`[0x67bfc9]`、`Respect_Gdiplus`→`[0x67bfca]`、`Respect_Ghostscript`→`[0x67bfcb]`。

開圖分派函式 `0x45a780`（CONFIRMED，反組譯 + 沙箱矩陣）。`isJpeg` = `0x455b10`（比對 `jpg`/`jpeg`），`gdipExt` = 副檔名在 map `0x68ec04` 且非 jpeg：
1. 動畫 GIF 偵測特殊路徑（`0x456ca0`）：對 `[Movie] Extensions` 內副檔名呼叫；真影片直接回 true，gif/cbz/zip 才呼叫 `0x451cb0` 讀內容列舉 block 數多幀。顯示閘門 `[0x68e4dd]`（`0x45a8a4`）**在 .data 恆 0**（零寫入）→ 結果永遠被丟棄、動畫顯示路徑 `0x45a8ad`+ 是死碼。已 patch 退場（`patch_hamana_gif.py`：`0x456d6c` 的 `call 0x451cb0` → `xor eax,eax`；見 `attack-surface.md`「GIF 退場」）
2. `Respect_libjpeg && isJpeg` → 內建 libjpeg `0x45a450`
3. `(Respect_internalPNGDec && ext==png) || Respect_Gdiplus` → 若 `gdipExt`：`ifgdip.spi`（物件 `0x68eb94`，經 `0x463630`）
4. 一般 SPI 迴圈（清單 `0x68eb5c`，每筆 0x80 bytes；`[+2]!=0` 的略過）
5. `!Respect_libjpeg && isJpeg` → 內建 libjpeg
6. 第 3 步沒試過且 `gdipExt` → `ifgdip.spi`
- 成功後 `isJpeg` 時呼叫 `0x456fc0`（CONFIRMED：EXIF 擷取，經 `0x452ee0`/`0x452f90`→libjpeg `0x452720` 讀 marker + 自寫 EXIF 解析；已由 `patch_hamana_exif_retire.py` 退場，見 `attack-surface.md`「EXIF 退場」）。
- `gdipExt` 的 map `0x68ec04` = ifgdip 物件（`0x68eb90`，載入時以匯出 `IsIfGDIP` 辨識，`0x464252`）宣告的副檔名集合；內建 libjpeg 以虛擬 SPI 項目註冊（`0x46453f`）。
- 推論：**jpg/jpeg 永遠不會交給 GDI+**（BMP 改名 `.jpg` 在所有設定都讀不出；改名 `.png` 則讀得出）；**exe 內沒有可達的 PNG 解碼器**，`Respect_internalPNGDec` 實際只決定 PNG 是否先問 GDI+。
- PNG 相關字串兩組皆在靜態連結的 D3DX 區（libpng `1.0.5` 與 PNG writer），無 code xref、無 libpng 錯誤字串 → 孤兒資料（guess）；開 PNG 時 zlib inflate（`0x4d6110`/`0x4d6760`）在任何設定都不命中（CONFIRMED，正控制組 = 原版開 zip 會命中）。

`DefaultSPIs/` 各 plugin 宣告的副檔名（自 binary 字串取得）：

| plugin | 宣告 | 備註 |
|---|---|---|
| `ifwebp.spi`（2024） | `*.webp` | uyjulian 版，支援 VP8 / VP8X |
| `ifwebp.spi_`（2011） | `*.webp` | libwebp 0.1.3，**不支援 VP8X**，勿使用 |
| `ifgdip.spi`（v0.8） | `*.bmp` `*.png` `*.gif` `*.tif;*.tiff` `*.ico` `*.wmf` `*.emf`；`*.jpg;*.jpeg` 只在 jpg 旗標=1 時 | GDI+。DllMain 讀 ini `[ifgdip]`（`dpi` `bmp` `png` `gif` `tif` `jpg` `ico` `wmf` `emf`，jpg 預設 0）。**ini 路徑 bug**：`GetModuleFileNameA` 後沒截掉檔名就接 `ifgdip.ini` → 實際讀 `DefaultSPIs\ifgdip.spiifgdip.ini`（CONFIRMED，`spi_probe.py` + 讀內部字串 `0x1001a0e0`）。`GetPicture` 不看副檔名，依內容解碼（CMYK JPEG 可） |
| `ax7z.spi` | 7z.dll 支援的壓縮格式 | **zip 不走它**，見下 |

## 壓縮檔處理

原生支援（`Hamana.txt` §1）：`zip / 7z / rar / arj / cab / cbz / cbr / lzh`。

**zip（含 cbz）走 Hamana 內建常式，不是 ax7z.spi。**
依據 `DefaultSPIs/ax7z.txt` §8：

> Hamana でパスワード付き zip を開けない。
> (Hamana では zip の処理が内蔵ルーチン優先のため。Susie プラグイン側では対応不可能)

內建實作是 **minizip 0.15（Gilles Vollant, 1998）+ zlib 1.1.4**（exe 內字串可證）。

「內建處理哪些副檔名」是由 `.rdata` 中一對相鄰的 **UTF-16 字串常數**決定的
（本版本 file offset）：

| offset | 內容 |
|---|---|
| 2236736 | `gif` |
| **2236744** | **`cbz`** |
| **2236752** | **`zip`** |
| 2236760 | `IgnoreExtensions`（ini key，可作定位錨點） |

把 `cbz` / `zip` 改成不可能出現的副檔名後，Hamana 找不到內建 handler，
**會自動把檔案交給 Susie SPI** —— 內建解壓整條退場。
這就是 `tools/patch_hamana_zip.py` 的作法（只改字串、不動程式碼、大小不變）。
驗證：patch 後開加密 zip 會彈出 `ax7z.spi password` 對話框，
而原版不會（`ax7z.txt` §8 將此列為 Hamana 無法對應的限制）。

以下為**未 patch** 時的行為。分派規則分三層，各不相同：

| 層級 | 判斷依據 |
|---|---|
| Hamana 決定交給誰 | **副檔名** |
| ax7z.spi 決定收不收 | **副檔名**（`ax7z.txt`：「拡張子によって対応形式かどうかを判別し中身を見ていません」） |
| `7z.dll` 實際解壓 | **檔案內容**（signature 偵測） |

最後一層是關鍵：**把 zip 改名成 `.rar` / `.7z` / `.arj`，Hamana 會交給 ax7z.spi，
7z.dll 再依內容認出它其實是 zip 並正常解開** —— 內建 zip 常式整條被跳過，
不需重新壓縮（實測 CONFIRMED，見 `docs/solutions.md` L1a）。

例外：`.cbz` 走內建路徑（仍崩潰）；`.cbr` 開不起來，應是被導向
`Formats/rar.dll`（2006 版，嚴格解析 rar 格式）。

`Formats/`（7z.dll、rar.dll、lzh.dll、arj.dll、cab.dll）與 `Codecs/` 為
7-zip 4.x 的 format / codec plugin；`DefaultSPIs/7z.dll` 則是 2024-11-30 版，
ax7z.spi optional 使用的是後者（同目錄優先）。

## 閃退定位（debugger 工法）

本機無 WinDbg / cdb；用 `tools/re/`（需 capstone、pefile，裝在專案 `.venv`）：

```bash
# 建環境（uv cache 不可落在 AppData）
UV_CACHE_DIR=<非 AppData 的 cache 目錄> uv venv --python <系統 python> .venv
UV_CACHE_DIR=<非 AppData 的 cache 目錄> uv pip install --python .venv capstone pefile
```

| 工具 | 用途 |
|---|---|
| `tools/re/minidbg.py` | ctypes 小 debugger（64-bit Python 可除 32-bit），`--bp VA` 命中時列暫存器與堆疊中的 return address（模組+偏移）。預設 bp `0x5bfbf4`。`--loads` 逐筆記錄 DLL 載入（含用完即釋放的），`--cwd` 指定工作目錄；**必須在 PowerShell 執行**（Git Bash 會弄壞參數 → `CreateProcess failed 2`） |
| `tools/re/disasm.py <exe> <VA> [back] [n]` | 反組譯 VA 附近 |
| `tools/re/make_sandbox.py <安裝目錄> <沙箱> [--exe Hamana.exe.orig] [--set K=V] [--drop-spi X]` | 建沙箱（自動套下方 ini 覆寫、去掉 Recent File List）；`reset_window()` 被 hidden_run / minidbg 每次啟動前呼叫 |
| `tools/re/spi_probe.py <plugin.spi> [檔案...]` | **需 32-bit Python**：`UV_PYTHON_INSTALL_DIR` 設到非 AppData 的工具目錄後 `uv python install cpython-3.12-windows-x86`。列 `GetPluginInfo`，對檔案跑 `IsSupported` / `GetPicture`（只印尺寸） |
| `tools/re/hidden_run.py` | **首選**：背景逐檔矩陣（最小化 + 不啟動，不搶焦點），多 exe 對照，輸出不含檔名；`--modules` 列結束時的非系統模組；作法與不可行方案見 docstring |
| `tools/re/run_sandbox.ps1` / `run_matrix.ps1` | 舊版（Start-Process，**會搶前景焦點**）；run_sandbox 仍可用於逐秒記錄記憶體 |
| `tools/re/pe_audit.py` | PE 安全旗標（ASLR/DEP/SafeSEH/GS）與內嵌函式庫指紋 |
| `tools/re/exif_check.py` / `make_exif_fuzz.py` | EXIF 結構檢查 / 產生損壞樣本 |

流程：事件記錄 Id=1000 看例外碼與位移 → 若是 `0xC000000D @ 0x1bfc24`，
`minidbg.py` 下預設 bp 開素材 → 堆疊第一個 Hamana 位址（`[esp+0x18]`）即越界的呼叫點 →
`disasm.py` 看該函式 prologue 是否為 `push -1; push thunk` 型 EH frame → 可套 L0b 手法。

事實（CONFIRMED）：
- 無 ASLR（DllCharacteristics 不含 DYNAMIC_BASE），ImageBase `0x400000`，`.text` file offset = VA − `0x400000`
- 靜態連結 VS2005 CRT：`_invalid_parameter` `0x5bfbf4`、`_invalid_parameter_noinfo` `0x5bfc18`，全檔 2784 個呼叫點
- 大量 LTCG 自訂呼叫慣例（以 edi / esi 傳參），看呼叫端要注意

## 沙箱測試工法

不要直接用使用者本尊的 Hamana 測試 —— `OpenLastImageAuto=1` 會自動開啟上次的圖
（可能是敏感素材），且測試會污染 `Hamana.ini` 的 Recent File List。

建立沙箱：複製 `Hamana.exe` / `Hamana.key` / `HamanaLang.dll` 與
`Codecs` `DefaultSPIs` `Formats` `BaseTextures` `HamanaShader` 到暫存目錄（約 6MB），
自備最小 `Hamana.ini`，關鍵覆寫：

```
Only1Instance=0        # 否則第二次啟動只會轉給既有實例，測不到
OpenLastImageAuto=0    # 避免自動開啟使用者素材
IsFullscreen=0         # 避免全螢幕干擾
ThumbDBDir=<沙箱內路徑>  # 不要污染正式的縮圖快取
HistoryCount=0
History2Count=0
[Window] 的 Left=-5000 Right=-3700 Top=-3000 Bottom=-2100  # 畫面外，配合 hidden_run.py
```

`[Recent File List]` 整段不要複製（含使用者路徑）。

注意：**開單一檔案 = 進入其所在資料夾並背景預讀鄰居**。要測「這一張」是否致命，
每個樣本放獨立資料夾（BUG-3 實測：壞樣本與正常樣本同資料夾時，連正常那張也崩）。
`ThumbDBDir` 留空時 Hamana 會在 exe 旁建 `HamanaThumb/`。

坑（CONFIRMED，發生過兩次）：Hamana 正常結束時會把視窗座標寫回 ini（例：開**不存在的檔**時它會自行結束）。若當時是最小化，`[Window]` 變成 `Left=-32000` `Right=-31840`（160x28）→ 之後每次啟動 D3D 以 client 高 0 建 device 失敗，跳「Direct3D裝置製作失敗」對話框（`hamana_d3d.log` 的 `BackBufferHeight = 0`）。現已自動處理：`hidden_run.py` / `minidbg.py` 啟動前呼叫 `make_sandbox.reset_window()` 改回座標，`hidden_run.py` 也會略過不存在的目標檔。minidbg 用 `SW_SHOWNOACTIVATE`（不最小化）。

### 崩潰判定

輪詢行程結束碼；`hidden_run.py` 以 `CreateProcess` + `SW_SHOWMINNOACTIVE` 啟動並用 `EnumWindows` 讀標題。
舊腳本（`run_sandbox.ps1` / `run_matrix.ps1`）用 `Start-Process -PassThru` + 偵測 `WerFault`，會搶焦點。
送按鍵翻頁（`SendKeys`、`PostMessage` VK_SPACE 到主視窗）實測無效，標題不變；未查原因。

### 讀圖成敗判定（不需觀看圖片內容）

**視窗標題即結果**，格式：

```
<檔名> (<第幾張>/<總數>) [<W>x<H>x<bpp>]  |  <第二張> [<W>x<H>x<bpp>] - Hamana
```

- 讀取成功 → 標題出現該檔名與 `[WxHxbpp]`
- 讀取失敗 → 標題停在**別的檔名**（Hamana 靜默跳過失敗的那張）
- `x24` = JPEG 路徑，`x32` = 帶 alpha 通道的 SPI 路徑（如 WebP）

此法可在完全不檢視像素內容的前提下驗證解碼結果，符合專案的敏感素材規範。

### 測試素材來源

用 `BaseTextures/marble.jpg`（Hamana 自帶，1024x1024，非使用者素材）複製改名即可，
不要拿使用者提供的 debug 素材做互動式測試。WebP 樣本用 ffmpeg 轉：

```bash
ffmpeg -i marble.jpg -c:v libwebp -q:v 80 sample.webp
```
