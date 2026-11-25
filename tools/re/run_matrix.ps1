# 逐檔測試矩陣：每個檔案分別用多個沙箱 Hamana.exe 開啟，記錄 CRASH / ALIVE 與視窗標題中的 [WxHxbpp]。
# 用法: pwsh run_matrix.ps1 -Exes <exe1>,<exe2> -Files <檔案或資料夾> [-Seconds 8]
# 標題只輸出 (i/n) 與 [WxHxbpp]，不輸出檔名（避免把素材名稱帶進記錄）。
param([string[]]$Exes, [string]$Files, [int]$Seconds = 8)
$list = if (Test-Path $Files -PathType Container) { Get-ChildItem $Files -File | Sort-Object Name | ForEach-Object FullName } else { @($Files) }
foreach ($f in $list) {
    $row = @((Split-Path $f -Leaf).PadRight(28))
    foreach ($exe in $Exes) {
        $p = Start-Process -FilePath $exe -ArgumentList "`"$f`"" -WorkingDirectory (Split-Path $exe) -PassThru
        $t0 = Get-Date; $title = ''; $res = $null
        while (((Get-Date) - $t0).TotalSeconds -lt $Seconds) {
            Start-Sleep -Milliseconds 500
            $p.Refresh()
            if ($p.HasExited) { $res = 'CRASH 0x{0:X8} @{1:N0}s' -f $p.ExitCode, ((Get-Date) - $t0).TotalSeconds; break }
            if (Get-Process WerFault -ErrorAction SilentlyContinue) { $res = 'WERFAULT'; }
            if ($p.MainWindowTitle) { $title = $p.MainWindowTitle }
        }
        if (-not $res) { $res = 'ALIVE'; Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue }
        if ($res -eq 'WERFAULT') { Start-Sleep 3; $p.Refresh(); if ($p.HasExited) { $res = 'CRASH 0x{0:X8}' -f $p.ExitCode } else { Stop-Process -Id $p.Id -Force } }
        Get-Process WerFault -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
        $m = [regex]::Match($title, '\((\d+/\d+)\)\s*(\[[^\]]+\])?')
        $row += ('{0,-26} {1,-22}' -f $res, ($(if ($m.Success) { "($($m.Groups[1].Value)) $($m.Groups[2].Value)" } else { '-' })))
        Start-Sleep -Milliseconds 300
    }
    $row -join ' | '
}
