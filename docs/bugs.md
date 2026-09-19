# Hamana 2006.06.19 已確認 bug

- 修復方案分層（含通用解）：`docs/solutions.md` ← **先看這個**
- 架構與測試工法：`docs/hamana-internals.md`
- 工具：`tools/hamana_fix.py`（素材 diagnose / fix）、`tools/patch_ifwebp.py`（SPI 分派 patch）

本檔只記錄兩個 bug 的根因與重現證據；「該怎麼修」以 `solutions.md` 為準。

---

## BUG-1 `ZIP_ZERO_ENTRY` — 開壓縮檔後數秒閃退

狀態：**CONFIRMED**（沙箱實機重現 + 修復驗證）

| 項目 | 內容 |
|---|---|
| 觸發條件 | 壓縮檔內存在 `file_size == 0` 的 entry |
| 症狀 | 開啟後數秒 WerFault 崩潰（背景預讀觸及該 entry 時） |
| **另一觸發路徑** | **光是「瀏覽」含問題壓縮檔的資料夾就會崩潰**（exit `0xC000000D`），不必開啟它 |
| 崩潰路徑 | Hamana **內建壓縮常式**（minizip 0.15 / 1998 + zlib 1.1.4 / 2002），非 ax7z.spi |
| 解壓成資料夾 | **不崩潰**（同一個 0-byte 檔案僅該張讀不出來） |
| 改名 `.cbz` | **無效**，cbz 同樣走內建 routine |
| `compress_type` | 無關（實測 stored=0 觸發；deflate 的 0-byte 亦屬同類） |

重現矩陣（沙箱實測）：

| 測項 | 內容 | 結果 |
|---|---|---|
| B-CONTROL | zip，無 0-byte entry | ALIVE 15s |
| B-REPRO | zip，含 0-byte stored entry | **WerFault → CRASH** |
| B-FOLDER | 同一組檔案解壓成資料夾 | ALIVE 12s，清單 10 張 |
| B-CBZ | 同一個 zip 改名 `.cbz` | **WerFault → CRASH** |
| B-RENAME | 同一個 zip 改名 `.rar` / `.7z` / `.arj` | **ALIVE，空 entry 被略過** |
| B-CBR | 同一個 zip 改名 `.cbr` | 不崩潰但**開不起來** |
| B-7Z | 同內容真的打包成 `.7z` | ALIVE 16s，空 entry 被略過 |
| B-FIX-A | 移除 0-byte entry | ALIVE 12s |
| B-FIX-B | 0-byte 換成 1x1 佔位 JPEG | ALIVE 12s，張數與順序保留 |

根因推定（`guess`，未反組譯驗證）：內建 zip routine 對 `size==0` 的 entry
未做防呆，取得空 buffer 後直接交給解碼 / 貼圖路徑 → 存取無效指標。
ax7z 作者在 `DefaultSPIs/ax7z.txt` §8 自述「Hamana 的 zip 處理優先使用內建
常式」，可解釋為何 SPI 端無法介入此路徑。

### 修復

**首選（L0，一勞永逸）**：patch Hamana.exe 的 `zip`/`cbz` 字串常數，
停用內建解壓，整條交給 ax7z.spi + 7z.dll。素材完全不必預處理，
連加密 zip 都順便能開了。

```bash
python tools/patch_hamana_zip.py "<Hamana>\Hamana.exe"
```

**不想改 exe 時（L1a，零成本）**：把副檔名從 `.zip` 改成 `.rar` / `.7z` / `.arj`，
**內容不必重壓**。7z.dll 依內容偵測格式，故 zip 內容照樣解得開。
注意此法下 `.cbz` 仍崩潰、`.cbr` 開不起來。詳見 `solutions.md`。

**替代（L3）**：不想改容器格式時，做資料面修復：

```bash
python tools/hamana_fix.py fix "<壓縮檔>" -o "<輸出目錄>"
```

預設 `--zero-policy placeholder`：0-byte entry 換成 1x1 JPEG，**保留張數與頁序**。
改用 `--zero-policy drop` 則直接移除該 entry（張數減一）。

---

## BUG-2 `EXT_MISMATCH` — 部分圖檔讀圖失敗（顯示 error）

狀態：**CONFIRMED**（沙箱實機重現 + 修復驗證）

| 項目 | 內容 |
|---|---|
| 觸發條件 | 檔案內容為 WebP，副檔名卻是 `.jpg` |
| 症狀 | 該張讀取失敗、顯示 error，Hamana 跳往下一張 |
| 根因 | **Hamana 依副檔名分派解碼器，不看檔案內容** |
| 實際錯誤 | libjpeg `Not a JPEG file: starts with 0x52 0x49`（`RI` = RIFF） |
| 解壓後 | **同樣失敗**（與壓縮無關，純粹格式分派問題） |
| `Respect_libjpeg=0` | **無效**（已實測，仍依副檔名分派） |

「第 187 張之後」不是資源耗盡或張數上限 —— 是該組素材第 187 個檔案起
**內容改為 WebP 但沿用 `.jpg` 副檔名**。前 186 張為真 JPEG，故正常。

重現矩陣（沙箱實測）：

| 測項 | 內容 | 結果 |
|---|---|---|
| A-NORMAL | 真 JPEG，`.jpg` | 讀取成功 `[WxHx24]` |
| A-CONTROL | WebP，`.webp` | 讀取成功 `[WxHx32]` |
| A-REPRO | WebP 內容，`.jpg` 副檔名 | **開不起來，跳往下一張** |
| A-FIX1 | 同上 + `Respect_libjpeg=0` | **仍失敗** |
| VERIFY | 正名為 `.webp` | 讀取成功 `[WxHx32]` |

WebP 由 `DefaultSPIs/ifwebp.spi` 處理，該 plugin 的 `GetPluginInfo` 只宣告
`*.webp`（binary 字串已確認），所以副檔名為 `.jpg` 時永遠選不到它。

### 修復

**首選（L2）**：patch `ifwebp.spi` 讓它同時宣告 `.jpg`，一次解決所有
WebP 副檔名錯置，日後不必再逐包處理素材：

```bash
python tools/patch_ifwebp.py "<Hamana>\DefaultSPIs\ifwebp.spi"
# 另需在 Hamana.ini 設 Respect_libjpeg=0，否則不生效
```

實測真 JPEG 與真 WebP 皆無退化（Hamana 在 SPI 回報不支援時會自動回退）。

**替代（L3）**：正名素材副檔名，不動 Hamana 安裝：

```bash
python tools/hamana_fix.py fix "<壓縮檔>" -o "<輸出目錄>"   # zip：重打包並正名
python tools/hamana_fix.py fix "<資料夾>"                    # 資料夾：就地正名
```

兩者前提皆為 `DefaultSPIs/ifwebp.spi` 存在（2024 版支援 VP8 與 VP8X；
2011 的 `ifwebp.spi_` 基於 libwebp 0.1.3，**不支援 VP8X**，勿回退）。

---

## 非 bug、但值得記錄的觀察

- `Hamana.exe` 為 32-bit 且 **未設 LARGE_ADDRESS_AWARE** → 使用者位址空間上限 2GB。
  大量高解析度圖片 + `UseMipMap=1`（+33% 記憶體）時仍有 OOM 風險，
  但本次兩個 bug **均與記憶體無關**，勿誤導後續排查方向。
- 內建 zlib 為 **1.1.4**（2002），版本極舊；本次崩潰的 entry 是 stored，
  未經 inflate，因此與 zlib 解壓缺陷無關。
