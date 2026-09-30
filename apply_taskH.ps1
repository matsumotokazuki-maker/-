# ============================================================
# タスクH 適用スクリプト（検証PC / inventory_app.py の2箇所のみ cherry-pick）
#   ①日付列を小数第1位表示  ②ceil削除で生の小数値
# 全置換しない・pkl は触らない。行番号でなくコードパターンで特定。
# 実行: PowerShell で本ファイルを右クリック→PowerShellで実行、または
#       powershell -ExecutionPolicy Bypass -File .\apply_taskH.ps1
# ============================================================

$ErrorActionPreference = 'Stop'

# --- 対象ファイル（検証PC のアプリ本体）---
$dist = 'C:\Users\kashidashi01\Desktop\inventory_app_dist'
$f = Join-Path $dist 'inventory_app.py'

if (-not (Test-Path $f)) {
    Write-Host "[STOP] inventory_app.py が見つかりません: $f" -ForegroundColor Red
    Write-Host "       パスを確認してください。" ; exit 1
}

# --- 1) バックアップ ---
$ts = Get-Date -Format 'yyyyMMdd_HHmmss'
$bak = "$f.bak_taskH_$ts"
Copy-Item $f $bak
Write-Host "[OK] バックアップ作成: $bak" -ForegroundColor Green

# --- 2) UTF-8 で読み込み（BOMなしを維持）---
$full = (Resolve-Path $f).Path
$raw = [System.IO.File]::ReadAllText($full, [System.Text.Encoding]::UTF8)

# --- 3) 置換対象（コードパターン）と置換後 ---
$old1 = 'else str(x))'
$new1 = 'else f"{float(x):.1f}")  # [タスクH]'
$old2 = 'math.ceil(qty / pack_size) if qty > 0 else 0'
$new2 = '(qty / pack_size) if qty > 0 else 0  # [タスクH-2] ceil削除'

# --- 4) 事前チェック（各1箇所だけ存在すること / 未適用であること）---
$c1 = ([regex]::Matches($raw, [regex]::Escape($old1))).Count
$c2 = ([regex]::Matches($raw, [regex]::Escape($old2))).Count
$already = ([regex]::Matches($raw, [regex]::Escape('float(x):.1f'))).Count
Write-Host "[CHECK] 編集箇所1 該当数=$c1 / 編集箇所2 該当数=$c2 / 既適用マーカー=$already"
if ($already -gt 0) {
    Write-Host "[STOP] 既にタスクHが適用済みのようです（float(x):.1f 検出）。重複適用を中止。" -ForegroundColor Yellow
    Remove-Item $bak ; exit 0
}
if ($c1 -ne 1 -or $c2 -ne 1) {
    Write-Host "[STOP] 対象が一意に見つかりません（c1=$c1 c2=$c2、想定は各1）。" -ForegroundColor Red
    Write-Host "       手順書 第3部の手動編集に切り替えてください。バックアップは残します。" ; exit 1
}

# --- 5) 置換（各1箇所のみ）---
$raw = $raw.Replace($old1, $new1)
$raw = $raw.Replace($old2, $new2)

# --- 6) UTF-8（BOMなし）で書き戻し ---
$enc = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($full, $raw, $enc)
Write-Host "[OK] 2箇所を置換しました。" -ForegroundColor Green

# --- 7) 検証：マーカー確認 ---
$chk = [System.IO.File]::ReadAllText($full, [System.Text.Encoding]::UTF8)
$m1 = ([regex]::Matches($chk, [regex]::Escape('[タスクH]'))).Count
$m2 = ([regex]::Matches($chk, [regex]::Escape('[タスクH-2] ceil削除'))).Count
$leftCeil = ([regex]::Matches($chk, [regex]::Escape('packs = math.ceil'))).Count
Write-Host "[CHECK] タスクH=$m1  タスクH-2=$m2  残存ceil(=0想定)=$leftCeil"

# --- 8) 構文チェック ---
Write-Host "[RUN] python -m py_compile ..."
& python -m py_compile $full
if ($LASTEXITCODE -eq 0 -and $m1 -ge 1 -and $m2 -ge 1 -and $leftCeil -eq 0) {
    Write-Host "`n[SUCCESS] タスクH 適用完了。アプリを再起動して画面を確認してください。" -ForegroundColor Green
    Write-Host "          バックアップ: $bak"
} else {
    Write-Host "`n[FAIL] 検証に失敗。バックアップから復元してください:" -ForegroundColor Red
    Write-Host "       Copy-Item `"$bak`" `"$f`" -Force"
    exit 1
}
