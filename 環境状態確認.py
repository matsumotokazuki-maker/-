# -*- coding: utf-8 -*-
# 在庫アプリ 環境状態レポート（検証PC×個人PCの突合用）
# 使い方：このファイルを inventory_app（検証PCは inventory_app_dist）フォルダに置いて
#   python 環境状態確認.py
# 実行後、同フォルダにできる「環境状態レポート_<PC名>.txt」を武田さんへ送ってください。
import os, socket, datetime, pickle, glob, json
from pathlib import Path
try:
    import pandas as pd
except Exception:
    pd = None

BASE = Path(__file__).resolve().parent
L = []
def P(s=""):
    L.append(str(s))

P("=" * 60)
P("在庫アプリ 環境状態レポート")
P("=" * 60)
P(f"PC名     : {socket.gethostname()}")
P(f"フォルダ : {BASE}")
P(f"作成     : {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

P("\n--- アプリ/スクリプトのバージョン ---")
for f in ['inventory_app.py', 'update_data.py', 'convert_raw_to_app.py']:
    p = BASE / f
    if p.exists():
        s = p.stat()
        P(f"  {f}: {s.st_size} bytes / 更新 {datetime.datetime.fromtimestamp(s.st_mtime).strftime('%Y-%m-%d %H:%M')}")
    else:
        P(f"  {f}: (なし)")
try:
    itxt = (BASE / 'inventory_app.py').read_text(encoding='utf-8', errors='ignore')
    P(f"  §O 自動退避(auto_quarantine): {'あり' if 'auto_quarantine_fullload_for_diff' in itxt else 'なし'}")
    P(f"  データモニタリング(_dm_scan): {'あり' if '_dm_scan_stockout' in itxt else 'なし'}")
except Exception as e:
    P(f"  inventory_app 判定エラー: {e}")
try:
    utxt = (BASE / 'update_data.py').read_text(encoding='utf-8', errors='ignore')
    P(f"  §P plan二重計上の除去   : {'あり' if '全列一致の重複行' in utxt else 'なし'}")
except Exception as e:
    P(f"  update_data 判定エラー: {e}")

P("\n--- pkl (data/session_data.pkl) ---")
pkl = BASE / 'data' / 'session_data.pkl'
if pkl.exists() and pd is not None:
    try:
        d = pickle.load(open(pkl, 'rb'))
        P(f"  last_update : {d.get('last_update')}")
        sts = d.get('stores', [])
        P(f"  stores({len(sts)}): {sts}")
        loaded = d.get('loaded', {})
        for k in ['material', 'recipe', 'inventory', 'plan', 'actual_prod', 'plan_del', 'actual_del']:
            v = loaded.get(k)
            if hasattr(v, '__len__'):
                rng = ""
                cols = list(v.columns) if hasattr(v, 'columns') else []
                for c in cols:
                    if str(c).endswith('_d'):
                        col = pd.to_datetime(v[c], errors='coerce')
                        if col.notna().any():
                            rng = f"  日付 {col.min().date()}..{col.max().date()}"
                            break
                extra = f"  完全重複行={int(v.duplicated().sum())}" if k == 'plan' else ""
                P(f"  {k}: {len(v)}行{rng}{extra}")
            else:
                P(f"  {k}: (なし)")
    except Exception as e:
        P(f"  pkl読込エラー: {e}")
else:
    P("  pklなし、または pandas未導入")

P("\n--- 取込フォルダの中身 ---")
for fld in ['business_system_export', 'input_raw']:
    p = BASE / fld
    fs = sorted([x.name for x in p.glob('*') if x.is_file()]) if p.exists() else []
    P(f"  {fld}: {len(fs)}件")
    for n in fs[:25]:
        P(f"      {n}")

P("\n--- data/ 設定ファイル ---")
for pat in ['managed_jans_*.json', 'adjustments.pkl']:
    for f in sorted(glob.glob(str(BASE / 'data' / pat))):
        info = f"{os.path.getsize(f)} bytes"
        if f.endswith('.json'):
            try:
                jj = json.load(open(f, encoding='utf-8'))
                info += f" / jans {len(jj.get('jans', []))}"
            except Exception:
                pass
        P(f"  {os.path.basename(f)}: {info}")

out = "\n".join(L)
print(out)
rep = BASE / f"環境状態レポート_{socket.gethostname()}.txt"
try:
    rep.write_text(out, encoding='utf-8')
    print(f"\n→ 保存しました: {rep}")
except Exception as e:
    print(f"保存エラー: {e}")
