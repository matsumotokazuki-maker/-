# -*- coding: utf-8 -*-
"""
棚卸カウント確認タブの「棚卸予定日」修正を、検証PCの inventory_app.py に当てるパッチ。

何をするか（3箇所だけ・他は一切触らない）:
  1) 棚卸予定日の既定値を「実績最終日」→「今日」に変更
  2) 見込み計算の実績/計画境界を「棚卸予定日」→「実績最終日」に固定
     （これが無いと棚卸予定日を今日にしても当日の計画製造が無視される）

安全設計:
  - 実行前に inventory_app.py を自動バックアップ（.bak_YYYYMMDD_HHMMSS_before_tana_patch）
  - 変更前の文字列が「ぴったり」存在する場合だけ置換（違えば中断＝壊さない）
  - すでに適用済みなら何もしないで終了（二重実行OK＝冪等）
  - 置換後に py_compile（文法チェック）まで実施
  - cp932 環境でも文字化け・例外で落ちないよう標準出力を UTF-8 化
"""
import sys
import io
import os
import shutil
import datetime
import py_compile

# cp932 端末でも絵文字・日本語の print で落ちないように UTF-8 化（過去の教訓）
try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
except Exception:
    pass

TARGET = "inventory_app.py"

# ---- 修正1：棚卸予定日の既定値（実績最終日 → 今日） ----
OLD1 = """        window_ok = (plan_max is not None) and (plan_max >= min_sel)
        default_sel = actual_max  # 既定は実績最終日（範囲内にクランプ）
        if plan_max is not None and default_sel > plan_max:
            default_sel = plan_max
        if default_sel < min_sel:
            default_sel = min_sel"""

NEW1 = """        window_ok = (plan_max is not None) and (plan_max >= min_sel)
        # 既定は「今日」（＝今夜カウントする想定日）。実績最終日(actual_max)は通常「昨日」のため、
        # 既定を actual_max にすると今日開いても予定日が昨日になり予習にならない（2026/6/9 武田指摘）。
        # 今日の製造は計画として下の target_date=actual_max 経由で反映される。範囲外はクランプ。
        default_sel = datetime.date.today()
        if plan_max is not None and default_sel > plan_max:
            default_sel = plan_max
        if default_sel < min_sel:
            default_sel = min_sel"""

# ---- 修正2：見込み計算の実績/計画境界（棚卸予定日 → 実績最終日に固定） ----
OLD2 = """                flow_tana = calculate_flow_cached(
                    selected_store,
                    target_date=period_end,
                    base_days=params['base_days'],
                    yield_fusoku=params['yield_fusoku'],
                    yield_kasoku=params['yield_kasoku'],
                    adjustments={},  # 見込みは物理カウントの予習のため、ズレ補正は加えない
                )"""

NEW2 = """                flow_tana = calculate_flow_cached(
                    selected_store,
                    # target_date は実績／計画の境界。棚卸予定日(period_end)ではなく実績最終日に固定する。
                    # period_end を渡すと、棚卸予定日が実績最終日より先のとき その間の日を「実績扱い→空」で
                    # 計上し、当日分の計画製造が無視される（見込み@予定日＝見込み@実績最終日になる）。
                    # 実績最終日で固定すれば 〜実績最終日=実績、それ以降=計画 で延伸し、
                    # 棚卸予定日(=今日)の製造終了後の見込みが正しく出る（2026/6/9 武田指摘）。
                    target_date=actual_max,
                    base_days=params['base_days'],
                    yield_fusoku=params['yield_fusoku'],
                    yield_kasoku=params['yield_kasoku'],
                    adjustments={},  # 見込みは物理カウントの予習のため、ズレ補正は加えない
                )"""


def main():
    print("=" * 60)
    print(" 棚卸予定日 修正パッチ（検証PC用）")
    print("=" * 60)

    if not os.path.exists(TARGET):
        print(f"[中断] このフォルダに {TARGET} が見つかりません。")
        print("      inventory_app.py と同じフォルダで実行してください。")
        return 1

    with open(TARGET, "r", encoding="utf-8") as f:
        src = original = f.read()

    # すでに適用済みか（冪等チェック）
    already1 = "default_sel = datetime.date.today()" in src
    already2 = "target_date=actual_max," in src
    if already1 and already2:
        print("[OK] すでに適用済みです。変更は不要です（何もしませんでした）。")
        return 0

    # 変更前の文字列が存在するか確認
    problems = []
    if not already1 and OLD1 not in src:
        problems.append("修正1の対象（既定値ブロック）が見つかりません。")
    if not already2 and OLD2 not in src:
        problems.append("修正2の対象（calculate_flow_cached の呼び出し）が見つかりません。")
    if problems:
        print("[中断] 検証PCのコードが想定と一致しません。安全のため何も変更しませんでした。")
        for p in problems:
            print("      - " + p)
        print("      → 武田さんに連絡してください（手動での確認が必要）。")
        return 2

    # バックアップ
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = f"{TARGET}.bak_{stamp}_before_tana_patch"
    shutil.copy2(TARGET, bak)
    print(f"[1/4] バックアップ作成: {bak}")

    # 置換
    n1 = n2 = 0
    if not already1:
        src = src.replace(OLD1, NEW1); n1 = 1
    if not already2:
        src = src.replace(OLD2, NEW2); n2 = 1
    print(f"[2/4] 置換: 修正1={'適用' if n1 else 'スキップ(適用済)'} / 修正2={'適用' if n2 else 'スキップ(適用済)'}")

    with open(TARGET, "w", encoding="utf-8") as f:
        f.write(src)
    print("[3/4] 書き込み完了")

    # 文法チェック
    try:
        py_compile.compile(TARGET, doraise=True)
        print("[4/4] 文法チェック: SYNTAX_OK")
    except py_compile.PyCompileError as e:
        # 失敗したらバックアップから戻す
        shutil.copy2(bak, TARGET)
        print("[失敗] 文法エラーが出たためバックアップから元に戻しました。")
        print("       " + str(e)[:300])
        print("       → 武田さんに連絡してください。")
        return 3

    print("-" * 60)
    print(" 完了しました。次はアプリを再起動して画面を確認してください。")
    print(f" 元に戻したい時は {bak} を inventory_app.py に上書きコピー。")
    print("-" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
