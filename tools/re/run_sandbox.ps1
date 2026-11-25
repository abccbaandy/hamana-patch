# 沙箱崩潰測試：啟動 Hamana 開檔，每秒記錄視窗標題、記憶體，偵測 WerFault / 結束碼。
# 用法: pwsh run_sandbox.ps1 -Exe <沙箱 Hamana.exe> -Target <檔案> [-Seconds 40]
# 注意：視窗標題含檔名；只在本機 console 輸出，不寫入專案。
param([string]$Exe, [string]$Target, [int]$Seconds = 40)
$p = Start-Process -FilePath $Exe -ArgumentList "`"$Target`"" -WorkingDirectory (Split-Path $Exe) -PassThru
$t0 = Get-Date
$last = ''
while (((Get-Date) - $t0).TotalSeconds -lt $Seconds) {
    Start-Sleep -Milliseconds 1000
    $p.Refresh()
    $sec = [int]((Get-Date) - $t0).TotalSeconds
    if ($p.HasExited) { "[{0,3}s] EXIT code=0x{1:X8}" -f $sec, $p.ExitCode; break }
    $wer = Get-Process WerFault -ErrorAction SilentlyContinue
    $title = $p.MainWindowTitle
    $resp = $p.Responding
    $line = "[{0,3}s] resp={1} ws={2}MB priv={3}MB wer={4} title={5}" -f $sec, $resp, [int]($p.WorkingSet64/1MB), [int]($p.PrivateMemorySize64/1MB), [bool]$wer, $title
    $line
}
if (-not $p.HasExited) { "ALIVE after $Seconds s"; Stop-Process -Id $p.Id -Force }
