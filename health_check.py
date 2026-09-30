# -*- coding: utf-8 -*-
"""
health_check.py  —  在庫アプリ 日次取込の健全性 自己点検（読み取り専用）

役割（「機械が気付かせて、人が動く」の実装）:
  - session_data.pkl を読むだけ（★pklは一切書き換えない）。
  - 正常日は静かに [健全性OK] 1行。異常日だけ [⚠️異常] を目立たせる。
  - 毎回 data/health_log.csv に1行追記（日次トレンドが一覧できる）。

点検項目:
  1. 店舗数        … pkl['stores'] が EXPECT_STORES(=9) か
  2. 仮JAN         … マスタ未突合(9991原料/9992製品) が 0 か  ← 情物一致の核
  3. 製造データ鮮度 … actual_prod の最新日が today から PROD_LAG_WARN 日以内か
  4. 全列一致重複  … plan / plan_del / inventory に冗長重複が無いか（§P）
  5. 空テーブル    … loaded の主要テーブルが空でないか
  6. 主要テーブルの行数 … ★2026-08-12 追加。行数＋その指紋（8桁）＋ホスト名を必ず出す
       なぜ必要か：2台運用（個人PC／検証PC）で「最新日は同じなのに中身が違う」が起きるため。
       2026-08-11 実測＝両PCとも [健全性OK] 製造最新2026-08-11 と同じ行を出したが、
       update_data のログ上 製造実績の累計は 個人PC 1,655 行 / 検証PC 1,770 行（8/10 の115行が個人PC側に無い）。
       ★最新日・店舗数・重複だけでは、この欠落を1件も検知できなかった。
       → 両PCのログを並べたとき「指紋」の8桁が違えば、それだけで中身が違うと分かる。
       ★ホスト名を出すのは、出力単体では どちらのPCで実行したかを証明できないため（CLAUDE.md §Z-5 の指摘）。

異常判定（[⚠️異常]）: 店舗数≠EXPECT / 仮JAN>0 / 重複>0 / 空テーブルあり
注意判定（[△注意]）: 製造ラグ >= PROD_LAG_WARN 日
それ以外          : [健全性OK]

使い方:
  python health_check.py
  （update_data の後に実行。health_check.bat 経由でも可。終了コードは常に0＝バッチを止めない）
"""
import sys, io, os, pickle, csv, hashlib, platform
from datetime import date, datetime

# stdout を utf-8 化（cp932 環境でも文字化けしない）
try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
except Exception:
    pass

# ---- 設定（必要なら数値だけ調整）----
EXPECT_STORES = 9          # 運用店舗数
PROD_LAG_WARN = 3          # 製造データが today からこの日数以上 古ければ「注意」
TANA_OLD_WARN = 45         # 棚卸が この日数以上 古ければ「注意」(情報)
DUP_CHECK_TABLES = ['plan', 'plan_del', 'inventory']  # 全列一致重複を見る（実績系は除外＝§P）
# ★行数を出すテーブルと表示名（2026-08-12 追加）。順序は指紋の計算順でもあるので変えない。
ROW_TABLES = ['actual_prod', 'actual_del', 'plan_del', 'plan', 'inventory', 'recipe', 'material']
ROW_SHORT = {'actual_prod': 'prod', 'actual_del': 'del', 'plan_del': 'pdel',
             'plan': 'plan', 'inventory': 'inv', 'recipe': 'rcp', 'material': 'mat'}

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# 既定は data/session_data.pkl。引数で別pkl（バックアップ点検・テスト）も指定可。
PKL = sys.argv[1] if len(sys.argv) > 1 else os.path.join(SCRIPT_DIR, 'data', 'session_data.pkl')
LOG = os.path.join(os.path.dirname(PKL), 'health_log.csv')  # ログはpklと同じフォルダ
# ★行数ログは別ファイルにする（2026-08-12）。既存 health_log.csv に列を足すと過去行と列数が合わなくなるため。
ROWLOG = os.path.join(os.path.dirname(PKL), 'health_rows.csv')

DATE_COL = {  # 各テーブルの「パース済み日付」列
    'actual_prod': '日付_d', 'actual_del': '納品予定日_d',
    'plan': '日付_d', 'plan_del': '納品予定日_d', 'inventory': '棚卸日_d',
}

def _to_date(v):
    if v is None: return None
    if isinstance(v, datetime): return v.date()
    if isinstance(v, date): return v
    try: return datetime.fromisoformat(str(v)[:10]).date()
    except Exception: return None

def maxdate(df, col):
    if col not in df.columns: return None
    s = df[col].dropna()
    if len(s) == 0: return None
    try: return _to_date(s.max())
    except Exception: return None

def main():
    today = date.today()
    issues, warns = [], []

    if not os.path.exists(PKL):
        print(f"[⚠️異常] session_data.pkl が見つかりません: {PKL}")
        _append_log(today, '', '', '', '', '', '異常(pkl無)')
        return 0

    with open(PKL, 'rb') as f:
        d = pickle.load(f)
    loaded = d.get('loaded', {})
    stores = d.get('stores', [])

    # 1. 店舗数
    nstore = len(stores)
    if nstore != EXPECT_STORES:
        issues.append(f"店舗数 {nstore}（想定{EXPECT_STORES}）")

    # 2. 仮JAN（JAN列を持つ全テーブル）
    fake_total = 0
    for k, df in loaded.items():
        if 'JAN' in df.columns:
            j = df['JAN'].astype(str)
            fake_total += int(j.str.startswith(('9991', '9992')).sum())
    if fake_total > 0:
        issues.append(f"仮JAN {fake_total}件（マスタ未突合）")

    # 3. 製造データ鮮度
    prod_max = maxdate(loaded.get('actual_prod'), DATE_COL['actual_prod']) if 'actual_prod' in loaded else None
    prod_lag = (today - prod_max).days if prod_max else None
    if prod_lag is not None and prod_lag >= PROD_LAG_WARN:
        warns.append(f"製造データが{prod_lag}日前（{prod_max}）")

    # 実納品・棚卸（情報）
    del_max = maxdate(loaded.get('actual_del'), DATE_COL['actual_del']) if 'actual_del' in loaded else None
    tana_max = maxdate(loaded.get('inventory'), DATE_COL['inventory']) if 'inventory' in loaded else None
    if tana_max:
        tana_age = (today - tana_max).days
        if tana_age >= TANA_OLD_WARN:
            warns.append(f"棚卸が{tana_age}日前（{tana_max}）")

    # 4. 全列一致重複
    dup_total = 0
    dup_detail = []
    for k in DUP_CHECK_TABLES:
        if k in loaded:
            try:
                nd = int(loaded[k].duplicated().sum())
            except Exception:
                nd = 0
            dup_total += nd
            if nd > 0: dup_detail.append(f"{k}:{nd}")
    if dup_total > 0:
        issues.append(f"全列一致重複 {dup_total}行（{', '.join(dup_detail)}）")

    # 5. 空テーブル
    empties = [k for k, df in loaded.items() if len(df) == 0]
    if empties:
        issues.append(f"空テーブル: {', '.join(empties)}")

    # 6. 主要テーブルの行数＋指紋（★2026-08-12 追加・判定はしない＝出すだけ）
    #    判定に使わないのは、行数の「正しい値」が日々変わるため。
    #    見るのは絶対値ではなく「2台で同じか」＝指紋の8桁を突き合わせる。
    rows = {}
    for k in ROW_TABLES:
        df = loaded.get(k)
        try:
            rows[k] = int(len(df)) if df is not None else None
        except Exception:
            rows[k] = None
    rows_str = ' '.join(
        f"{ROW_SHORT[k]}={rows[k] if rows[k] is not None else '-'}" for k in ROW_TABLES)
    rows_fp = hashlib.md5(
        ';'.join(f"{k}={rows[k]}" for k in ROW_TABLES).encode('utf-8')).hexdigest()[:8]
    host = os.environ.get('COMPUTERNAME') or platform.node() or '?'
    rows_line = f"   行数[{host}] {rows_str}  →指紋 {rows_fp}"

    # ---- 判定・出力 ----
    prod_str = f"{prod_max}({prod_lag}日前)" if prod_max else "なし"
    summary = (f"店舗{nstore} | 仮JAN{fake_total} | 製造最新{prod_str} | "
               f"実納品{del_max or 'なし'} | 棚卸{tana_max or 'なし'} | 重複{dup_total}")

    if issues:
        status = '異常'
        print(f"\n[⚠️異常] {today}  ← 確認が必要です")
        for x in issues: print(f"   ・{x}")
        if warns:
            for w in warns: print(f"   （注意）{w}")
        print(f"   詳細: {summary}")
    elif warns:
        status = '注意'
        print(f"\n[△注意] {today}  {summary}")
        for w in warns: print(f"   ・{w}")
    else:
        status = 'OK'
        print(f"\n[健全性OK] {today}  {summary}")

    # ★行数は 異常/注意/OK のどの日も必ず出す（OKの日にこそ2台のズレが隠れるため）
    print(rows_line)

    _append_log(today, nstore, fake_total, prod_str, del_max, tana_max, status, dup_total)
    _append_rows_log(today, host, rows, rows_fp)
    return 0

def _append_log(today, nstore, fake, prod, deliv, tana, status, dup=''):
    new = not os.path.exists(LOG)
    try:
        with open(LOG, 'a', newline='', encoding='utf-8-sig') as f:
            w = csv.writer(f)
            if new:
                w.writerow(['点検日', '店舗数', '仮JAN', '製造最新', '実納品最新', '棚卸最新', '全列重複', '状態'])
            w.writerow([today, nstore, fake, prod, deliv, tana, dup, status])
    except Exception as e:
        print(f"   [warn] health_log 追記に失敗: {e}")

def _append_rows_log(today, host, rows, fp):
    """★2026-08-12 追加。行数の日次トレンドを別ファイルに残す。
    別ファイルにする理由＝既存 health_log.csv に列を足すと、過去行と列数が合わず読めなくなるため。"""
    new = not os.path.exists(ROWLOG)
    try:
        with open(ROWLOG, 'a', newline='', encoding='utf-8-sig') as f:
            w = csv.writer(f)
            if new:
                w.writerow(['点検日', 'ホスト'] + [ROW_SHORT[k] for k in ROW_TABLES] + ['指紋'])
            w.writerow([today, host] + [rows.get(k) for k in ROW_TABLES] + [fp])
    except Exception as e:
        print(f"   [warn] health_rows 追記に失敗: {e}")

if __name__ == '__main__':
    try:
        rc = main()
    except Exception as e:
        print(f"\n[⚠️異常] 点検スクリプトでエラー: {e}")
        rc = 0  # バッチを止めない
    sys.exit(rc)
