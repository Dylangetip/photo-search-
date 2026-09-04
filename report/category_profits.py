"""Total realised profit per category, speculative stock only (presold excluded)."""
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
tr['tcost']=pd.to_numeric(tr['SoldForFinalCost'],errors='coerce')
SALES=tr[['ItemNumber','TimeStamp','price','tcost']]

def build(sub):
    s=sub[(sub['received']>=WINDOW)&(~sub['is_sample'])&sub['bc'].notna()&(sub['bc']>0)]
    s=s.sort_values('received').drop_duplicates('inventory_number',keep='last')
    m=s[['inventory_number','received','bc','majorclass_name']].merge(SALES,left_on='inventory_number',right_on='ItemNumber',how='left')
    m=m[m['TimeStamp'].isna()|(m['TimeStamp']>=m['received'])]
    m=m[m['TimeStamp'].isna()|(m['price']>0)]
    f=m.sort_values(['inventory_number','TimeStamp']).groupby('inventory_number',as_index=False).first()
    f['sold']=f['TimeStamp'].notna(); f['days']=(f['TimeStamp']-f['received']).dt.days
    f['tcost']=f['tcost'].fillna(f['bc']); f['profit']=f['price']-f['tcost']
    return f[~(f['sold']&(f['days']<=PRESOLD))]        # drop presold / special orders

def sub(**kw):
    x=inv
    if 'classes' in kw: x=x[x['majorclass_code'].isin(kw['classes'])]
    if kw.get('labcenter') is not None: x=x[x['_labcenter']==kw['labcenter']]
    if kw.get('anylab') is not None: x=x[x['_labdia']==kw['anylab']]
    up=lambda c: x[c].fillna('').astype(str).str.upper()
    if kw.get('citizen'):
        x=x[up('vendor_name').str.contains('CITIZEN')|up('watch_brand').str.contains('CITIZEN')|up('description').str.contains('CITIZEN')]
    if kw.get('noncitizen'):
        x=x[~(up('vendor_name').str.contains('CITIZEN')|up('watch_brand').str.contains('CITIZEN')|up('description').str.contains('CITIZEN'))]
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
 ('Citizen watches',sub(classes=[60,61,62],citizen=True)),
 ('Watches - other brands',sub(classes=[60,61,62],noncitizen=True))]

rows=[]
for name,s in CATS:
    f=build(s); sold=f[f['sold']]
    if len(f)<20: continue
    rows.append(dict(category=name,units=len(f),units_sold=len(sold),
        profit_total=sold['profit'].sum(),
        profit_per_piece=sold['profit'].mean() if len(sold) else np.nan,
        margin=100*sold['profit'].sum()/sold['price'].sum() if sold['price'].sum() else np.nan,
        med_days=sold['days'].median()))
df=pd.DataFrame(rows)
spec=pd.read_pickle('lg/cats_spec.pkl')[['category','ret6m','margin','med_days','units']]
chk=df.merge(spec,on='category',suffixes=('','_ref'))
chk['margin_diff']=(chk['margin']-chk['margin_ref']).abs()
chk['days_diff']=(chk['med_days']-chk['med_days_ref']).abs()
chk['unit_diff']=(chk['units']-chk['units_ref']).abs()
print("VALIDATION against cats_spec.pkl (max diffs should be ~0)")
print("  margin %.4f   med_days %.4f   units %d"%(chk.margin_diff.max(),chk.days_diff.max(),chk.unit_diff.max()))
out=chk.sort_values('ret6m',ascending=False)
print()
print("%-38s %8s %7s %13s %11s"%("category","units","sold","profit made","per piece"))
for _,r in out.iterrows():
    print("%-38s %8d %7d %13s %11s"%(r.category,r.units,r.units_sold,
        format(r.profit_total,',.0f'),format(r.profit_per_piece,',.0f')))
df.to_pickle('lg/cat_profits.pkl')
