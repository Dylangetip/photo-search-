"""Loose lab grown diamonds: the memo line, measured on its own terms."""
import warnings; warnings.filterwarnings('ignore')
import pandas as pd, numpy as np, sys, re, json
sys.path.insert(0,'lg')
from ident import load

ASOF=pd.Timestamp('2026-08-19'); WINDOW=pd.Timestamp('2021-01-01'); PRESOLD=14
DIA=[70,71,72,73,74,75,76,77,79]
inv=load()
inv['received']=pd.to_datetime(inv['received_date'],errors='coerce')
inv['bc']=pd.to_numeric(inv['composite_cost'],errors='coerce')

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
    m=s[['inventory_number','received','bc']].merge(SALES,left_on='inventory_number',right_on='ItemNumber',how='left')
    m=m[m['TimeStamp'].isna()|(m['TimeStamp']>=m['received'])]
    m=m[m['TimeStamp'].isna()|(m['price']>0)]
    f=m.sort_values(['inventory_number','TimeStamp']).groupby('inventory_number',as_index=False).first()
    f['sold']=f['TimeStamp'].notna(); f['days']=(f['TimeStamp']-f['received']).dt.days
    f['tcost']=f['tcost'].fillna(f['bc']); f['profit']=f['price']-f['tcost']
    f['age']=(ASOF-f['received']).dt.days
    return f

lab=build(inv[inv['majorclass_code'].isin(DIA)&(inv['_labdia']==True)])
sold=lab[lab['sold']]; unsold=lab[~lab['sold']]
pre=sold[sold['days']<=PRESOLD]; spec=sold[sold['days']>PRESOLD]

R=dict(
 taken=len(lab), sold=len(sold), unsold=len(unsold),
 sell_through=100*len(sold)/len(lab),
 profit_all=sold['profit'].sum(), profit_pre=pre['profit'].sum(), profit_spec=spec['profit'].sum(),
 per_stone=sold['profit'].mean(),
 revenue=sold['price'].sum(),
 margin=100*sold['profit'].sum()/sold['price'].sum(),
 med_days_all=sold['days'].median(), med_days_spec=spec['days'].median(), med_days_pre=pre['days'].median(),
 n_pre=len(pre), pct_pre=100*len(pre)/len(sold),
 n_spec=len(spec), pct_spec=100*len(spec)/len(sold),
 avg_price=sold['price'].mean(), avg_cost=sold['tcost'].mean(),
 avg_profit=sold['profit'].mean(),
 cost_on_hand=unsold['bc'].sum(),
 sold_6m=int((sold['days']<=183).sum()), pct_6m=100*(sold['days']<=183).sum()/len(sold),
 sold_90=int((sold['days']<=90).sum()), pct_90=100*(sold['days']<=90).sum()/len(sold),
 aged1y=int((unsold['age']>365).sum()),
 span_yrs=(ASOF-WINDOW).days/365.25)
R['profit_per_yr']=R['profit_all']/R['span_yrs']
R['taken_per_yr']=R['taken']/R['span_yrs']

for tag,g in (('pre',pre),('spec',spec)):
    R['avg_price_'+tag]=g['price'].mean(); R['avg_cost_'+tag]=g['tcost'].mean()
    R['avg_profit_'+tag]=g['profit'].mean()
    R['margin_'+tag]=100*g['profit'].sum()/g['price'].sum()

for k,v in R.items():
    print("%-16s %s"%(k, format(v,',.1f') if isinstance(v,float) else v))
json.dump({k:(float(v) if isinstance(v,(int,float,np.floating)) else v) for k,v in R.items()},
          open('lg/memo.json','w'),indent=1)
