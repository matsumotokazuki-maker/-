# -*- coding: utf-8 -*-
# 個人PC/検証PC の両方で同じコマンドで実行し、出力を突き合わせる
#   python verify_pkl_fingerprint.py
import pickle, sys, hashlib
import pandas as pd
try: sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception: pass
d=pickle.load(open('data/session_data.pkl','rb'))
L=d['loaded']
def h(df):
    try:
        return hashlib.md5(pd.util.hash_pandas_object(df.fillna(''),index=False).values.tobytes()).hexdigest()[:10]
    except Exception:
        return 'NA'
print("=== pkl 指紋（両PCで一致すべき値）===")
print("店舗数 :", len(d.get('stores',[])))
inv=L['inventory']
print("棚卸日 :", sorted(set(str(x) for x in inv['棚卸日_d'].dropna().unique())))
ap=L['actual_prod']
ck=ap[ap['店舗名'].astype(str).str.contains('粕屋',na=False)] if '店舗名' in ap else ap.iloc[0:0]
print("粕屋CK製造max :", str(ck['日付_d'].dropna().max()) if len(ck) else 'なし')
ad=L['actual_del'].copy(); ad['n']=pd.to_numeric(ad['仕入計上数'],errors='coerce')
print("actual_del 負行 :", int((ad['n']<0).sum()))
print("行数  : inv=%d recipe=%d material=%d plan=%d actual_prod=%d actual_del=%d plan_del=%d" % (
    len(inv),len(L['recipe']),len(L['material']),len(L.get('plan',[])),len(ap),len(L['actual_del']),len(L.get('plan_del',[]))))
print("内容hash(版差でズレ得るので参考): inv=%s recipe=%s material=%s" % (h(inv),h(L['recipe']),h(L['material'])))

# --- コード指紋（2026-08-07 追加） -------------------------------------------
# 目的：両PCで別々にフルロードする運用（§S案X をやめる場合）を安全にする。
#   pkl の SHA256 は last_update(datetime) が埋まるため、中身が同じでも必ず不一致になる。
#   → データの一致は上の行数/内容hashで、コードの一致はここで見る。
#   ★データが一致してもコードがズレていれば「たまたま同じ入力で同じ結果になっただけ」なので、
#     両方そろって初めて「同じ環境で同じ結果」と言える。
import os
_CODE_FILES = [
    'update_data.py',
    'inventory_app.py',
    'convert_raw_to_app.py',
    'health_check.py',
    'verify_pkl_fingerprint.py',
]
print("=== コード指紋（両PCで一致すべき値）===")
for _f in _CODE_FILES:
    if os.path.exists(_f):
        _b = open(_f, 'rb').read()
        print("  %-28s %8d B  md5=%s" % (_f, len(_b), hashlib.md5(_b).hexdigest()[:10]))
    else:
        print("  %-28s %8s     %s" % (_f, '-', '🔴 なし（このPCに配置されていない）'))
