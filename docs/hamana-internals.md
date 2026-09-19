# Hamana 內部構造與測試工法

供後續 session 快速進入狀況。bug 本身見 `docs/bugs.md`。

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

`DefaultSPIs/` 各 plugin 宣告的副檔名（自 binary 字串取得）：

| plugin | 宣告 | 備註 |
|---|---|---|
| `ifwebp.spi`（2024） | `*.webp` | uyjulian 版，支援 VP8 / VP8X |
| `ifwebp.spi_`（2011） | `*.webp` | libwebp 0.1.3，**不支援 VP8X**，勿使用 |
| `ifgdip.spi` | `*.bmp` `*.jpg;*.jpeg` `*.png` `*.gif` `*.tif;*.tiff` `*.ico` `*.wmf` `*.emf` | GDI+ |
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
```

### 崩潰判定

`Start-Process -PassThru` 後輪詢 `$p.HasExited`，並偵測 `WerFault` 行程出現。
腳本範例留在 session scratchpad `hs/test_crash.ps1`（非專案檔，需要時重寫即可）。

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
