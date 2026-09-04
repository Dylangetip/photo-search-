"""Rebuild the category economics from raw POS lines and stress the margin figure."""
import warnings; warnings.filterwarnings('ignore')
import pandas as pd, numpy as np, sys, re
sys.path.insert(0,'lg')
from ident import load
from center import is_lab_center

ASOF=pd.Timestamp('2026-08-19'); WINDOW=pd.Timestamp('2021-01-01'); PRESOLD=14
inv=load()
inv['received']=pd.to_datetime(inv['received_date'],errors='coerce')
inv['bc']=pd.to_numeric(inv['composite_cost'],errors='coerce')
lgmap=dict(zip(inv['inventory_number'],inv['_labdia']))
skupat=re.compile(r'\b(\d{2}-\d{3}-\d{2,4})\b')
def ref_lab(s):
    for k in skupat.findall(str(s).upper()):
        if lgmap.get(k,False): return True
    return False
d=inv['description'].fillna('')
inv['_reflab']=d.map(ref_lab)
inv['_labcenter']=[is_lab_center(t,q) for t,q in zip(d,inv['_reflab'])]

tr=pd.read_csv('POS_Transactions.csv',low_memory=False)
sl=pd.read_csv('POS_SalesSlip.csv',low_memory=False)
sl['TimeStamp']=pd.to_datetime(sl['TimeStamp'],errors='coerce')
tr=tr[tr['TransactionType']==1].merge(sl[['ID','TimeStamp','Cancelled']],left_on='SalesSlipID',right_on='ID',how='left')
tr=tr[tr['Cancelled']!=1]
tr['ItemNumber']=tr['ItemNumber'].astype(str)
tr['price']=pd.to_numeric(tr['SoldForPrice'],errors='coerce').fillna(0.0)
tr['tcost_raw']=pd.to_numeric(tr['SoldForFinalCost'],errors='coerce')
SALES=tr[['ItemNumber','TimeStamp','price','tcost_raw']]

def build(sub):
    s=sub[(sub['received']>=WINDOW)&(~sub['is_sample'])&sub['bc'].notna()&(sub['bc']>0)]
    s=s.sort_values('received').drop_duplicates('inventory_number',keep='last')
    m=s[['inventory_number','received','bc']].merge(SALES,left_on='inventory_number',right_on='ItemNumber',how='left')
    m=m[m['TimeStamp'].isna()|(m['TimeStamp']>=m['received'])]
    m=m[m['TimeStamp'].isna()|(m['price']>0)]
    f=m.sort_values(['inventory_number','TimeStamp']).groupby('inventory_number',as_index=False).first()
    f['sold']=f['TimeStamp'].notna(); f['days']=(f['TimeStamp']-f['received']).dt.days
    f['cost_filled']=f['tcost_raw'].isna()&f['sold']
    f['tcost']=f['tcost_raw'].fillna(f['bc'])
    f['profit']=f['price']-f['tcost']
    return f[~(f['sold']&(f['days']<=PRESOLD))]

def sub(**kw):
    x=inv
    if 'classes' in kw: x=x[x['majorclass_code'].isin(kw['classes'])]
    if kw.get('labcenter') is not None: x=x[x['_labcenter']==kw['labcenter']]
    if kw.get('anylab') is not None: x=x[x['_labdia']==kw['anylab']]
    up=lambda c: x[c].fillna('').astype(str).str.upper()
    if kw.get('citizen'): x=x[up('vendor_name').str.contains('CITIZEN')|up('watch_brand').str.contains('CITIZEN')|up('description').str.contains('CITIZEN')]
    if kw.get('noncitizen'): x=x[~(up('vendor_name').str.contains('CITIZEN')|up('watch_brand').str.contains('CITIZEN')|up('description').str.contains('CITIZEN'))]
    return x

DIA=[70,71,72,73,74,75,76,77,79]
CATS=[('Engagement rings - lab centre',sub(classes=[10],labcenter=True)),
 ('Engagement rings - lab accents only',sub(classes=[10],labcenter=False,anylab=True)),
 ('Engagement rings - natural',sub(classes=[10],anylab=False)),
 ('Anniversary bands - lab',sub(classes=[11],anylab=True)),
 ('Anniversary bands - natural',sub(classes=[11],anylab=False)),
 ('Ladies rings',sub(classes=[12])),('Mens bands',sub(classes=[20])),
 ('Loose lab-grown diamonds',sub(classes=DIA,anylab=True)),
 ('Loose natural diamonds',sub(classes=DIA,anylab=False)),
 ('Earrings',sub(classes=[32])),('Pendants',sub(classes=[31])),
 ('Necklaces',sub(classes=[30])),('Bracelets',sub(classes=[40])),
 ('Citizen watches',sub(classes=[60,61,62],citizen=True))]

rows=[]; flags=[]
for name,s in CATS:
    f=build(s); sold=f[f['sold']]
    if len(f)<20: continue
    rev=sold['price'].sum(); cost=sold['tcost'].sum(); prof=sold['profit'].sum()
    m_agg=100*prof/rev                                  # weighted: total profit / total revenue
    m_mean=100*(sold['profit']/sold['price']).mean()    # unweighted mean of per item margins
    m_markup=100*prof/cost                              # margin ON COST (markup), the wrong one
    rows.append(dict(category=name,n=len(sold),revenue=rev,cost=cost,profit=prof,
        avg_rev=sold['price'].mean(),avg_cost=sold['tcost'].mean(),avg_profit=sold['profit'].mean(),
        margin=m_agg,margin_unweighted=m_mean,markup_on_cost=m_markup,
        med_days=sold['days'].median(),
        zero_cost=int((sold['tcost']<=0).sum()),
        neg_profit=int((sold['profit']<0).sum()),
        cost_estimated=int(sold['cost_filled'].sum()),
        pct_cost_est=100*sold['cost_filled'].mean()))
df=pd.DataFrame(rows)

print("### IDENTITY CHECK: does revenue - cost = profit, and avg_rev - avg_cost = avg_profit?")
e1=(df['revenue']-df['cost']-df['profit']).abs().max()
e2=(df['avg_rev']-df['avg_cost']-df['avg_profit']).abs().max()
e3=(100*df['profit']/df['revenue']-df['margin']).abs().max()
print("   max error: totals %.9f   averages %.9f   margin %.9f"%(e1,e2,e3))

print("\n### DATA QUALITY that could distort margin")
print("%-38s %5s %9s %9s %9s"%("category","sold","cost<=0","neg profit","cost estimated"))
for _,r in df.iterrows():
    print("%-38s %5d %9d %10d %9d (%.0f%%)"%(r.category,r.n,r.zero_cost,r.neg_profit,r.cost_estimated,r.pct_cost_est))

print("\n### MARGIN THREE WAYS (report uses 'on revenue', weighted)")
print("%-38s %12s %12s %12s"%("category","on revenue","unweighted","markup on cost"))
for _,r in df.sort_values('margin',ascending=False).iterrows():
    print("%-38s %11.1f%% %11.1f%% %11.1f%%"%(r.category,r.margin,r.margin_unweighted,r.markup_on_cost))
df.to_pickle('lg/cat_econ.pkl')
