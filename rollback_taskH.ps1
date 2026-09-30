# ============================================================
# タスクH ロールバックスクリプト（検証PC）
#   直近の .bak_taskH_* バックアップから inventory_app.py を復元する。
# 実行: powershell -ExecutionPolicy Bypass -File .\rollback_taskH.ps1
# ============================================================
$ErrorActionPreference = 'Stop'
$dist = 'C:\Users\kashidashi01\Desktop\inventory_app_dist'
$f = Join-Path $dist 'inventory_app.py'

$baks = Get-ChildItem (Join-Path $dist 'inventory_app.py.bak_taskH_*') -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending
if (-not $baks) {
    Write-Host "[STOP] バックアップ(.bak_taskH_*)が見つかりません。" -ForegroundColor Red ; exit 1
}
$latest = $baks[0]
Write-Host "復元元: $($latest.FullName)"
Copy-Item $latest.FullName $f -Force
& python -m py_compile $f
if ($LASTEXITCODE -eq 0) {
    Write-Host "[OK] 復元完了（タスクH前へ）。アプリを再起動してください。" -ForegroundColor Green
} else {
    Write-Host "[FAIL] py_compile NG。手動で確認してください。" -ForegroundColor Red ; exit 1
}
