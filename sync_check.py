# -*- coding: utf-8 -*-
"""
sync_check.py — 在庫アプリ 取込ルーティンの「連続性」自己点検（読み取り専用）

■ なぜ作ったか（2026-08-13）
  health_check.py は「その日の中身」を見る装置。**その日 実行されたかどうかは見ていない。**
  実測＝health_log.csv に 2026-08-11(火) が無い。同 8/09(日)・8/02(日)・7/26(日) も無い。
  2026-08-12 に判明した「個人PCの日次取込が4日間止まっていて誰も気づかなかった」は、
  ★ログには穴が空いていたのに、穴を見る装置が無かった、というだけの話だった。
  → これは [装置は在るが効いていない] の同型。**在る記録を読むだけで検知できる。**

■ 何を見るか（すべて既存ログを読むだけ・pklも業務データも一切書き換えない）
  1. 実行の抜け     … 直近N日で「1回も実行されていない日」を曜日つきで列挙
  2. 実行回数       … 週あたり何回 人が回したか（📐 二階の測定＝人が手を動かした回数/週）
  3. データの停滞   … 指紋が前回と同じ＝実行はしたが中身が増えていない日
  4. pkl の鮮度     … 自分の session_data.pkl がいつのものか
  5. 2台の突合(任意) … --peer で相手PCの health_rows.csv を渡すと、どのテーブルが何行違うかを出す
                       ★機械が比べる。人が8桁を目で見比べる必要をなくすのが目的。

■ 使い方
    python sync_check.py                      … 直近30日を点検
    python sync_check.py --days 60            … 期間を変える
    python sync_check.py --peer D:\\health_rows.csv   … 相手PCのログと突合

■ 設計上の約束（既存 health_check.py に合わせる）
  - 読み取り専用。終了コードは常に0（バッチを止めない）。
  - 判定はするが、決めつけない。日曜・公休で抜けるのは正常なので「曜日」を必ず添えて人に返す。
"""
import sys, io, os, csv, pickle
from datetime import date, datetime, timedelta

try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
except Exception:
    pass

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, 'data')
HEALTH_LOG = os.path.join(DATA_DIR, 'health_log.csv')
ROW_LOG = os.path.join(DATA_DIR, 'health_rows.csv')
PKL = os.path.join(DATA_DIR, 'session_data.pkl')

WD = ['月', '火', '水', '木', '金', '土', '日']
ROW_SHORT = ['prod', 'del', 'pdel', 'plan', 'inv', 'rcp', 'mat']
ROW_FULL = {'prod': '製造実績', 'del': '実納品', 'pdel': '納品予定', 'plan': '計画数',
            'inv': '棚卸', 'rcp': 'レシピ', 'mat': '原料マスタ'}


def _read_csv(path):
    """utf-8-sig / cp932 のどちらでも読む（health_* は utf-8-sig で書かれている）。"""
    if not os.path.exists(path):
        return []
    for enc in ('utf-8-sig', 'cp932'):
        try:
            with open(path, 'r', encoding=enc, newline='') as f:
                return list(csv.DictReader(f))
        except Exception:
            continue
    return []


def _to_date(s):
    try:
        return datetime.fromisoformat(str(s)[:10]).date()
    except Exception:
        return None


def _fmt(d):
    return f"{d} ({WD[d.weekday()]})"


def main():
    days = 30
    peer = None
    a = sys.argv[1:]
    for i, v in enumerate(a):
        if v == '--days' and i + 1 < len(a):
            try:
                days = int(a[i + 1])
            except Exception:
                pass
        if v == '--peer' and i + 1 < len(a):
            peer = a[i + 1]

    today = date.today()
    since = today - timedelta(days=days - 1)
    host = os.environ.get('COMPUTERNAME') or '?'

    print(f"\n=== 取込ルーティン 連続性チェック [{host}] {today} / 直近{days}日 ===")

    logs = _read_csv(HEALTH_LOG)
    rows = _read_csv(ROW_LOG)

    if not logs:
        print(f"[⚠️] health_log.csv が読めません: {HEALTH_LOG}")
        return 0

    # ---- 実行日を集計 ----
    runs = {}   # date -> 回数
    status_of = {}  # date -> 最後の状態
    for r in logs:
        d = _to_date(r.get('点検日'))
        if not d:
            continue
        runs[d] = runs.get(d, 0) + 1
        status_of[d] = r.get('状態', '')

    all_days = sorted(runs)
    print(f"\n■ 母数＝health_log.csv 実行 {len(logs)}回 / 記録の最小日 {all_days[0]} 〜 最大日 {all_days[-1]}")

    # ---- 1. 実行の抜け ----
    missing = []
    d = since
    while d <= today:
        if d not in runs:
            missing.append(d)
        d += timedelta(days=1)
    # 当日は朝の時点でまだ実行前でありうるので分けて扱う
    today_pending = today in missing
    missing_past = [m for m in missing if m != today]

    print(f"\n■ 1. 実行の抜け（直近{days}日）")
    if not missing_past:
        print("   ✅ 抜けなし")
    else:
        print(f"   🔴 {len(missing_past)}日 実行なし")
        for m in missing_past:
            print(f"      ・{_fmt(m)}")
        # 連続抜けの最長
        longest, cur, prev = 0, 0, None
        for m in missing_past:
            cur = cur + 1 if (prev and (m - prev).days == 1) else 1
            longest = max(longest, cur)
            prev = m
        print(f"   → 最長の連続抜け＝{longest}日")
        print("   ※ 日曜・公休で抜けるのは正常。**曜日を見て、業務日の抜けだけを拾う。**")
    if today_pending:
        print(f"   （本日 {_fmt(today)} はまだ実行記録なし＝朝の時点なら正常）")

    # ---- 2. 実行回数（📐 二階の測定） ----
    print(f"\n■ 2. 人が回した回数（📐 二階の測定＝人が手を動かした回数/週）")
    weeks = {}
    for d0, c in runs.items():
        if d0 < since:
            continue
        monday = d0 - timedelta(days=d0.weekday())
        weeks[monday] = weeks.get(monday, 0) + c
    for w in sorted(weeks):
        print(f"   {w} 週 ： {weeks[w]:>3} 回")
    # ★金曜は「日次差分 ＋ 週次フルロード」で2回が構造的に正常（CLAUDE.md §V・週次運用 2026-07-03）。
    #   ここを一律に警告すると毎週 誤検知して読まれなくなる＝狼少年になる。金曜の2回だけは正常扱い。
    multi = sorted([d0 for d0, c in runs.items() if c >= 2 and d0 >= since])
    unexpected = [d0 for d0 in multi if not (d0.weekday() == 4 and runs[d0] == 2)]
    friday_ok = [d0 for d0 in multi if d0 not in unexpected]
    if friday_ok:
        print(f"   ✅ 金曜の2回＝{len(friday_ok)}日（日次差分＋週次フルロード＝構造的に正常）")
    if unexpected:
        print(f"   ⚠️ 想定より多い日＝{len(unexpected)}日 "
              f"（{', '.join(f'{_fmt(m)}{runs[m]}回' for m in unexpected)}）")
        print("      → やり直し・作業の重複の可能性。無人実行になれば 1日1回に収束する。")
    if not multi:
        print("   ✅ 複数回実行なし")

    # ---- 3. データの停滞（指紋が変わらない） ----
    print(f"\n■ 3. データの停滞（指紋が前回と同じ＝実行したが中身が増えていない）")
    if not rows:
        print(f"   （health_rows.csv がまだ無い／記録が浅い: {ROW_LOG}）")
    else:
        seq = []
        for r in rows:
            d0 = _to_date(r.get('点検日'))
            if d0 and d0 >= since:
                seq.append((d0, r.get('ホスト', '?'), r.get('指紋', '')))
        stalled = []
        prev_fp = None
        for d0, h, fp in seq:
            if prev_fp is not None and fp == prev_fp:
                stalled.append((d0, h, fp))
            prev_fp = fp
        if not stalled:
            print("   ✅ 停滞なし（指紋は毎回変化している）")
        else:
            print(f"   ⚠️ 指紋が前回と同じ＝{len(stalled)}回")
            for d0, h, fp in stalled:
                print(f"      ・{_fmt(d0)} [{h}] 指紋 {fp}")
            print("      ※ 同じ日に2回流した直後は同一になるのが正常。**日をまたいで同じなら止まっている。**")

    # ---- 4. pkl の鮮度 ----
    print(f"\n■ 4. session_data.pkl の鮮度")
    if not os.path.exists(PKL):
        print(f"   🔴 pkl が見つかりません: {PKL}")
    else:
        mt = datetime.fromtimestamp(os.path.getmtime(PKL))
        age = (today - mt.date()).days
        mark = '✅' if age <= 1 else ('⚠️' if age <= 3 else '🔴')
        print(f"   {mark} 最終更新 {mt:%Y-%m-%d %H:%M}（{age}日前） / {os.path.getsize(PKL):,} B")
        if age >= 2:
            print("      → 検証PC→個人PC の pkl コピーが要る可能性。★個人PCは分析側なので、"
                  "遅れても壊れないが、粕屋CKは週次コピーでしか復元できない。")

    # ---- 5. 2台の突合（任意） ----
    print(f"\n■ 5. 2台の突合")
    if not peer:
        print("   （--peer <相手PCの health_rows.csv> を渡すと、機械が突合します）")
        print("   ★現状 検証PC への共有パスは不通（2026-08-13 実測＝445は開通・C$ は Access denied・公開共有0件）。")
        print("     当面は相手ログをファイルで渡す運用。チャネルができれば同じコマンドがそのまま使える。")
    elif not os.path.exists(peer):
        print(f"   🔴 相手ログが見つかりません: {peer}")
    else:
        mine = rows[-1] if rows else None
        prs = _read_csv(peer)
        theirs = prs[-1] if prs else None
        if not mine or not theirs:
            print("   🔴 どちらかのログが空です")
        else:
            # ★同じ日どうしで比べる。日が違うログを突き合わせると「不一致」に見えるが
            #   それは中身の差ではなく "時点の差" ＝ 誤警報になる（2026-08-13 設計時に潰した穴）。
            my_d = mine.get('点検日')
            same_day = [r for r in prs if r.get('点検日') == my_d]
            aligned = bool(same_day)
            if aligned:
                theirs = same_day[-1]
            print(f"   自分 [{mine.get('ホスト')}] {my_d} 指紋 {mine.get('指紋')}")
            print(f"   相手 [{theirs.get('ホスト')}] {theirs.get('点検日')} 指紋 {theirs.get('指紋')}")
            if not aligned:
                print(f"   ⚠️⚠️ **点検日が違う**（自分 {my_d} / 相手 {theirs.get('点検日')}）")
                print("        → この差は『中身の差』ではなく『時点の差』の可能性がある。")
                print("        → 同じ日の行どうしで比べないと判定にならない。**下の判定は参考値**。")
            if mine.get('指紋') == theirs.get('指紋'):
                print("   ✅ 一致＝2台は同じ中身" + ("" if aligned else "（ただし点検日が違う＝参考）"))
            else:
                print("   🔴 不一致。テーブル別の差は下記のとおり：")
                any_diff = False
                for k in ROW_SHORT:
                    try:
                        a1, b1 = int(mine.get(k) or 0), int(theirs.get(k) or 0)
                    except Exception:
                        continue
                    if a1 != b1:
                        any_diff = True
                        print(f"      ・{ROW_FULL[k]}({k}) 自分 {a1:,} / 相手 {b1:,} "
                              f"＝差 {a1 - b1:+,} 行")
                if not any_diff:
                    print("      （行数はすべて同じ。指紋だけ違う場合は列順・記録形式の差を疑う）")
                if aligned:
                    print("   → **同じ点検日での不一致＝実際に中身が違う。**どちらが正かを決めて揃える。")
    print("")
    return 0


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print(f"\n[⚠️] sync_check でエラー: {e}")
    sys.exit(0)
