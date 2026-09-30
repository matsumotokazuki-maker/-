# -*- coding: utf-8 -*-
"""
タスクE: 5/31月末棚卸スナップショットを inventory テーブルに「差分追加」する一回限りの補助スクリプト。

設計判断（2026-06-02）:
  - update_data.py には「inventory 差分追加経路」が既に存在する
    （DIFF_ALLOWED_KINDS の '棚卸':'inventory' → process_diff_load → merge_diff）。
    JAN正規化・運用店舗フィルタ・日付キー正規化・空行除去まで実装済みで実績がある。
  - しかし input_raw フォルダには日次差分の残置ファイル（製造数/実納品数/納品予定数）が多数あり、
    `python update_data.py` をそのまま実行すると detect_mode がそれら全部を拾って
    他テーブルを再マージしてしまう（＝「他テーブルに一切手を触れない」制約に反する）。
  - そこで detect_mode を経由せず、process_diff_load に「棚卸ファイル1件だけ」を渡して
    既存の取込ロジックを使う。update_data.py 本体には一切手を入れない。

これにより:
  - inventory に 5/31 行が新規追加される（5/14 行はキーが衝突しないため保持）
  - 他6テーブル(plan/actual_prod/actual_del/plan_del/recipe/material)は不変
    （process_diff_load は loaded['inventory'] のみ書換え、save_loaded_to_pkl は冪等）
"""
import sys
import io
import pathlib

# 文字化け・cp932絵文字クラッシュ回避（個人PCの慣例）
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

import update_data as U  # 本体は読むだけ（改変しない）

# 5/31 棚卸データ（業務システム生ファイルの定位置）
CSV = pathlib.Path("business_system_export/棚卸データ.csv")

if not CSV.exists():
    print(f"[ERROR] 棚卸CSVが見つかりません: {CSV}")
    sys.exit(1)

# (path, date_str, kind) — date_str は表示用のみ。実際の日付は CSV の棚卸日列が決める。
diff_files = [(CSV, "20260531", "inventory")]

print(f"[INFO] inventory 差分追加: {CSV}")
ok = U.process_diff_load(diff_files)
print(f"[INFO] process_diff_load -> {ok}")
sys.exit(0 if ok else 2)
