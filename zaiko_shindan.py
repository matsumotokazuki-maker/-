# -*- coding: utf-8 -*-
"""
「今日の余裕日数」が空になる原因を、検証PC上で1回で切り分ける診断。
読み取りのみ。データは一切書き換えない。

使い方は 余裕日数_診断.bat をダブルクリック（同じフォルダに置くこと）。
"""
import sys, os, json, pickle, datetime

# Windows コンソール(cp932)で落ちないようにする
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

try:
    import pandas as pd
except ImportError:
    print('[NG] pandas がありません。アプリと同じ python で実行してください。')
    sys.exit(1)

HOUZAI_KUBUN = {'包材(本体)', '包材（蓋）', '包材(内装)', 'シール'}
BASE_DAYS, YF, YK = 3, 0.7, 0.9

here = os.path.dirname(os.path.abspath(__file__))
cands = [os.path.join(here, 'data', 'session_data.pkl'),
         os.path.join(here, 'inventory_app_dist', 'data', 'session_data.pkl'),
         os.path.join(here, '..', 'data', 'session_data.pkl')]
PKL = next((p for p in cands if os.path.exists(p)), None)
if PKL is None:
    print('[NG] session_data.pkl が見つかりません。data フォルダのある場所に置いてください。')
    for p in cands:
        print('     探した場所:', p)
    sys.exit(1)
DATA_DIR = os.path.dirname(PKL)

print('=' * 60)
print('  余裕日数 診断  (読み取りのみ)')
print('=' * 60)
print('pkl      :', PKL)
print('pkl更新  :', datetime.datetime.fromtimestamp(os.path.getmtime(PKL)))
print('OSの今日 :', datetime.date.today())
print('python   :', sys.version.split()[0], '/ pandas', pd.__version__)
print()

d = pickle.load(open(PKL, 'rb'))
L = d['loaded']
print('last_update:', d.get('last_update'))
print('行数:', {k: (len(v) if hasattr(v, "__len__") else '-') for k, v in L.items()})
print()

print('■ 管理対象JAN 設定ファイル')
found = False
for f in sorted(os.listdir(DATA_DIR)):
    if f.startswith('managed_jans_'):
        found = True
        p = os.path.join(DATA_DIR, f)
        try:
            n = len(json.load(open(p, encoding='utf-8')).get('jans', []))
        except Exception as e:
            n = 'READ-ERROR ' + str(e)
        print('   %s : %s件  (更新 %s)' % (f, n, datetime.datetime.fromtimestamp(os.path.getmtime(p))))
if not found:
    print('   (なし＝全原料表示)')
print()

inv, plan, aprod = L['inventory'], L['plan'], L['actual_prod']
pdel, adel = L['plan_del'], L['actual_del']
rec, mat = L['recipe'], L['material']
for df, c in ((plan, '計画数'), (aprod, '製造数'), (pdel, '納品予定数'),
              (adel, '仕入計上数'), (inv, '棚卸数'), (rec, '使用量')):
    df[c] = pd.to_numeric(df[c], errors='coerce').fillna(0)

print('■ plan 全体（全店）')
print('   行数:', len(plan), ' 日付:', plan['日付_d'].min(), '〜', plan['日付_d'].max())
print('   棚卸 最新:', inv['棚卸日_d'].max(), ' 製造実績 最新:', aprod['日付_d'].max())
print()


def calculate_flow(store, target_date):
    inv_s = inv[inv['店舗名'] == store]
    plan_s = plan[plan['店舗名'] == store]
    aprod_s = aprod[aprod['店舗名'] == store]
    pdel_s = pdel[pdel['店舗名'] == store]
    adel_s = adel[adel['店舗名'] == store]
    if len(inv_s) == 0 or inv_s['棚卸日_d'].dropna().empty:
        return pd.DataFrame(), None, None
    od = inv_s['棚卸日_d'].dropna().max()
    pdts = plan_s['日付_d'].dropna()
    if pdts.empty:
        return pd.DataFrame(), od, None
    ed = pdts.max()
    dr, cur = [], od + datetime.timedelta(days=1)
    while cur <= ed:
        dr.append(cur); cur += datetime.timedelta(days=1)
    midx = mat.set_index('原料JAN')
    rbm = {}
    for _, r in rec.iterrows():
        rbm.setdefault(r['原料JAN'], []).append((r['JAN'], float(r['使用量'])))
    op = inv_s[inv_s['棚卸日_d'] == od].groupby('JAN')['棚卸数'].sum().to_dict()
    tj = set(op) | set(rbm)
    if len(pdel_s): tj |= set(pdel_s['JAN'].dropna().unique())
    if len(adel_s): tj |= set(adel_s['JAN'].dropna().unique())
    tj &= set(midx.index)
    PD, AP, PL, AD = {}, {}, {}, {}
    for _, x in plan_s.iterrows():
        k = (x['JAN'], x['日付_d']); PL[k] = PL.get(k, 0) + float(x['計画数'])
    for _, x in aprod_s.iterrows():
        k = (x['JAN'], x['日付_d']); AP[k] = AP.get(k, 0) + float(x['製造数'])
    for _, x in pdel_s.iterrows():
        k = (x['JAN'], x['納品予定日_d']); PD[k] = PD.get(k, 0) + float(x['納品予定数'])
    for _, x in adel_s.iterrows():
        k = (x['JAN'], x['納品予定日_d']); AD[k] = AD.get(k, 0) + float(x['仕入計上数'])
    rows = []
    for mj in tj:
        mi = midx.loc[mj]
        if isinstance(mi, pd.DataFrame): mi = mi.iloc[0]
        try: ny = float(mi['入数'])
        except Exception: ny = 1
        if ny == 0: ny = 1
        kub = mi.get('原料区分', ''); ish = kub in HOUZAI_KUBUN
        uses = rbm.get(mj, []); remain = float(op.get(mj, 0.0))
        for dt_ in dr:
            if target_date is not None and dt_ <= target_date:
                inq = AD.get((mj, dt_), 0.0); src = AP
            else:
                inq = PD.get((mj, dt_), 0.0); src = PL
            out = 0.0
            for pj, u in uses:
                q = src.get((pj, dt_), 0.0)
                if q > 0: out += (q * u) if ish else (q * u / ny)
            remain += inq - out
            rows.append({'原料JAN': mj, '原料区分': kub, '日付': dt_,
                         '出': out, '残': remain})
    return pd.DataFrame(rows), od, ed


TODAY = datetime.date.today()
NG = []
for store in d['stores']:
    print('=' * 60)
    print('■ 店舗:', store)
    ap_s = aprod[aprod['店舗名'] == store]
    tmax = ap_s['日付_d'].max() if len(ap_s) and ap_s['日付_d'].notna().any() else aprod['日付_d'].max()
    dfp, od, ed = calculate_flow(store, None)
    dfa, _, _ = calculate_flow(store, tmax)
    print('   実績指定日(既定)=%s  期首(最新棚卸日)=%s  計画の最終日=%s' % (tmax, od, ed))
    if len(dfp) == 0 or len(dfa) == 0:
        print('   -> [計算データが揃っていません] が出る'); NG.append((store, '計算データ無し')); continue
    print('   df_plan=%d行 (%s〜%s)  df_actual=%d行' % (len(dfp), dfp['日付'].min(), dfp['日付'].max(), len(dfa)))
    cfg = None
    for p in (os.path.join(DATA_DIR, 'managed_jans_%s.json' %
                           "".join(c for c in store if c.isalnum() or c in ('-', '_'))),
              os.path.join(DATA_DIR, 'managed_jans_common.json')):
        if os.path.exists(p):
            try:
                cfg = json.load(open(p, encoding='utf-8'))
                print('   使う設定: %s (%d件)' % (os.path.basename(p), len(cfg.get('jans', []))))
                break
            except Exception:
                pass
    if cfg and cfg.get('jans'):
        b1, b2 = len(dfp), len(dfa)
        dfp = dfp[dfp['原料JAN'].isin(cfg['jans'])]
        dfa = dfa[dfa['原料JAN'].isin(cfg['jans'])]
        print('   絞り込み後: df_plan %d->%d行 / df_actual %d->%d行' % (b1, len(dfp), b2, len(dfa)))
    else:
        print('   使う設定: なし（全原料）')
    if len(dfp) == 0 or len(dfa) == 0:
        print('   -> [計算データが揃っていません] が出る'); NG.append((store, '絞り込みで全滅')); continue
    we = TODAY + datetime.timedelta(days=7)
    pw = dfp[(dfp['日付'] >= TODAY) & (dfp['日付'] <= we)]
    print('   [1] 今週窓(%s〜%s) の df_plan 行数 = %d' % (TODAY, we, len(pw)))
    upd = pw.groupby('原料JAN')['出'].mean().reset_index()
    upd.columns = ['原料JAN', '1日使用量']
    n_pos = int((upd['1日使用量'] > 0).sum()) if len(upd) else 0
    print('   [2] usage_per_day = %d件 / うち 1日使用量>0 = %d件' % (len(upd), n_pos))
    if TODAY in set(dfa['日付'].values):
        cur_ = dfa[dfa['日付'] == TODAY]; ref = TODAY
    else:
        past = dfa[dfa['日付'] <= TODAY]['日付']
        ref = past.max() if len(past) else dfa['日付'].min()
        cur_ = dfa[dfa['日付'] == ref]
    print('   [3] 現在残の基準日 = %s / current = %d件' % (ref, len(cur_)))
    cur_ = cur_[['原料JAN', '原料区分', '残']].rename(columns={'残': '現在残'})
    buf = cur_.merge(upd, on='原料JAN', how='left')
    buf['1日使用量'] = buf['1日使用量'].fillna(0)
    act = buf[buf['1日使用量'] > 0]
    print('   [4] merge後 = %d件 / df_active = %d件' % (len(buf), len(act)))
    if len(act) == 0:
        print('   [NG] ->「使用予定がある原料がありません」が出る')
        print('        ・今週窓が0行か？               -> %s' % (len(pw) == 0))
        print('        ・出>0 が0件か？                -> %s' % (n_pos == 0))
        print('        ・currentとusage_per_dayのJANが重ならないか？ -> %s'
              % (len(set(cur_['原料JAN']) & set(upd['原料JAN'])) == 0))
        NG.append((store, '余裕日数が空'))
    else:
        a = act.copy(); a['余裕日数'] = a['現在残'] / a['1日使用量']
        print('   [OK] -> 表が出る（危険<2日 %d件）' % int((a['余裕日数'] < 2).sum()))
        print('        区分別:', a.groupby('原料区分')['原料JAN'].nunique().to_dict())

print('=' * 60)
print('■ まとめ')
if NG:
    print('   空になる店舗: %d / %d' % (len(NG), len(d['stores'])))
    for s, why in NG:
        print('     -', s, ':', why)
    print('   => データ側に原因がある。上の [NG] 行の3つの判定を見る。')
else:
    print('   全%d店で「表が出る」判定。' % len(d['stores']))
    print('   => データは正常。画面が空ならアプリ側（キャッシュ／実行状態）。')
    print('      アプリを停止 → start_app.bat で再起動して確認。')
print('=' * 60)
