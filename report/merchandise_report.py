import warnings; warnings.filterwarnings('ignore')
import pandas as pd, sys, io
sys.path.insert(0,'lg')
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import (BaseDocTemplate, PageTemplate, Frame, Paragraph, Spacer,
                                Table, TableStyle, PageBreak, KeepTogether)

NAVY=colors.HexColor('#1F3864'); GOLD=colors.HexColor('#A8842C'); TEAL=colors.HexColor('#2E7D8F')
CREAM=colors.HexColor('#FBF6E9'); GREY=colors.HexColor('#F4F4F4')
LINE=colors.HexColor('#B0B0B0'); BLACK=colors.HexColor('#111111'); GREEN=colors.HexColor('#EAF1E4')
W=7.0*inch

# ---- data -------------------------------------------------------------
import json
M=json.load(open('lg/memo.json'))
c=pd.read_pickle('lg/cats_spec.pkl')
p=pd.read_pickle('lg/cat_profits.pkl')[['category','units_sold','profit_total','profit_per_piece']]
c=c.merge(p,on='category',how='left').sort_values('ret6m',ascending=False).reset_index(drop=True)
DROP=['Watches - other brands']
MEMO=['Loose lab-grown diamonds']
NICE={'Anniversary bands - lab':'Lab anniversary bands',
      'Engagement rings - lab centre':'Lab center engagement rings',
      'Loose lab-grown diamonds':'Loose lab grown diamonds',
      'Engagement rings - lab accents only':'Lab accent engagement rings',
      'Anniversary bands - natural':'Natural anniversary bands',
      'Engagement rings - natural':'Natural engagement rings'}
c=c[~c.category.isin(DROP)].reset_index(drop=True)
c['label']=c.category.map(lambda x: NICE.get(x,x))
NCAT=len(c)
CT=c[c.category=='Citizen watches'].iloc[0]
LC=c[c.category=='Engagement rings - lab centre'].iloc[0]
AB=c[c.category=='Anniversary bands - lab'].iloc[0]
AC=c[c.category=='Engagement rings - lab accents only'].iloc[0]
LD=c[c.category=='Loose lab-grown diamonds'].iloc[0]

TITLE=ParagraphStyle('T',fontName='Helvetica-Bold',fontSize=19,textColor=NAVY,leading=23,spaceAfter=2)
SUBT =ParagraphStyle('S',fontName='Helvetica',fontSize=10,textColor=colors.HexColor('#555555'),leading=13.5)
HEAD =ParagraphStyle('H',fontName='Helvetica-Bold',fontSize=13,textColor=NAVY,leading=16,spaceBefore=14,spaceAfter=6)
LEAD =ParagraphStyle('L',fontName='Helvetica',fontSize=11.5,textColor=BLACK,leading=16,spaceAfter=7)
NOTE =ParagraphStyle('N',fontName='Helvetica',fontSize=9,textColor=colors.HexColor('#404040'),leading=12.5)

def m(v,d=0): return '${:,.{p}f}'.format(v,p=d)
def sec(*f): return KeepTogether(list(f))
def box(t,fill=CREAM,border=GOLD,st=None):
    x=Table([[Paragraph(t,st or LEAD)]],colWidths=[W])
    x.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),fill),('BOX',(0,0),(-1,-1),1.2,border),
      ('LEFTPADDING',(0,0),(-1,-1),13),('RIGHTPADDING',(0,0),(-1,-1),13),
      ('TOPPADDING',(0,0),(-1,-1),9),('BOTTOMPADDING',(0,0),(-1,-1),6)])); return x
def grid(rows,widths,rowh=None,fs=9.5,hfs=9.5,cols=True,extra=None):
    t=Table(rows,colWidths=widths,rowHeights=rowh,repeatRows=1)
    st=[('FONT',(0,0),(-1,0),'Helvetica-Bold',hfs),('BACKGROUND',(0,0),(-1,0),NAVY),
        ('TEXTCOLOR',(0,0),(-1,0),colors.white),('FONT',(0,1),(0,-1),'Helvetica',fs),
        ('FONT',(1,1),(-1,-1),'Helvetica-Bold',fs),('ALIGN',(1,0),(-1,-1),'CENTER'),
        ('VALIGN',(0,0),(-1,-1),'MIDDLE'),('GRID',(0,0),(-1,-1),.6,LINE),
        ('LEFTPADDING',(0,0),(-1,-1),8),('TOPPADDING',(0,0),(-1,-1),4),('BOTTOMPADDING',(0,0),(-1,-1),4)]
    if cols: st+=[('TEXTCOLOR',(1,1),(1,-1),GOLD),('TEXTCOLOR',(2,1),(2,-1),TEAL)]
    for r in range(1,len(rows)):
        if r%2==0: st.append(('BACKGROUND',(0,r),(-1,r),GREY))
    for e in (extra or []): st.append(e)
    t.setStyle(TableStyle(st)); return t

def build_story():
    story=[]
    # ---------------- PAGE 1 ----------------
    story.append(Paragraph('Lab Grown Rings, Citizen Watches, and Where to Put the Money', TITLE))
    story.append(Paragraph('Rings with a lab grown <b>center stone</b> &nbsp;&bull;&nbsp; merchandise bought '
      'January 2021 to August 2026 &nbsp;&bull;&nbsp; Orem store POS data', SUBT))
    story.append(Spacer(1,11))
    story.append(box(
     '<b>THE ANSWER</b><br/><br/>'
     '<b>1. Lab center rings beat Citizen watches badly.</b> A ring sells in about 3&frac12; months, a watch in '
     '10. We bought half as many rings and made close to four times the profit.<br/><br/>'
     '<b>2. On an equal $25,000, six months in:</b> rings return <b>' + m(10100) + '</b>, watches return '
     '<b>' + m(2609) + '</b>.<br/><br/>'
     '<b>3. Lab bands are the best return of anything we pay for.</b> Lab anniversary bands make '
     '<b>50&#162; on the dollar</b> in six months and turn in 92 days, ahead of lab center rings at 40&#162;. '
     'Citizen is last of ' + str(NCAT) + '.<br/><br/>'
     '<b>4. Loose lab grown diamonds cost us nothing to carry.</b> We take them on memo and owe nothing until '
     'they sell, so stocking them is not a spending decision at all. It is the most profitable line in the '
     'store and it needs no budget. Keep taking them, and see page 2.'))

    story.append(sec(
     Paragraph('RINGS vs. CITIZEN WATCHES', HEAD),
     grid([['','Lab center\nrings','Citizen\nwatches','Rings vs. watches'],
       ['Pieces bought','97','179','half as many'],
       ['Pieces sold','40','94',''],
       ['Typical time to sell','109 days','297 days','2.7x faster'],
       ['Sold in first 6 months','1 in 3','1 in 6','1.9x'],
       ['Average ticket','$2,322','$306','7.6x'],
       ['Profit per piece sold','$1,148','$130','8.9x'],
       ['Total profit kept','$45,935','$12,181','3.8x'],
       ['Margin','49%','42%',''],
       ['Still unsold today','32 pieces','49 pieces',''],
       ['Of those, over 2 years old','1','25','']],
      [2.25*inch,1.45*inch,1.45*inch,1.85*inch],
      extra=[('BACKGROUND',(0,7),(-1,7),GREEN)]),
     Spacer(1,5),
     Paragraph('The shaded row is the headline. The last row is the one that should worry you: one unsold ring '
               'has sat over two years; twenty five watches have.', NOTE)))

    # ---------------- PAGE 2 ----------------
    story.append(PageBreak())
    story.append(sec(
     Paragraph('IF WE PUT $25,000 INTO EACH', HEAD),
     grid([['','Cash back','Profit','Return','Still in stock'],
       ['Rings at 6 months','$18,182','$10,100','40%','$16,917'],
       ['Rings at 1 year','$22,545','$11,104','44%','$13,559'],
       ['Watches at 6 months','$6,256','$2,609','10%','$21,352'],
       ['Watches at 1 year','$11,747','$5,312','21%','$18,565'],
       ['Watches at 2 years','$17,366','$6,482','26%','$14,116']],
      [1.9*inch,1.3*inch,1.25*inch,1.05*inch,1.5*inch]),
     Spacer(1,5),
     Paragraph('$25,000 buys about 22 rings or 129 watches. The rings make more in six months ($10,100) than the '
               'watches make in two full years ($6,482). Buying gradually instead of all at once lowers both, to '
               '$6,208 and $1,567 at month six, but not the gap. There is no two year ring figure because only 28 '
               'lab center pieces have been on the shelf that long.', NOTE)))

    story.append(sec(
     Paragraph('MEMO STONES: LOOSE LAB GROWN DIAMONDS', HEAD),
     box('These come in on memo. We owe nothing until one sells, so no part of a buying budget goes to them '
         'and they compete with nothing else on this page. Since 2021 they have made <b>' + m(M['profit_all']) +
         '</b>, about <b>' + m(M['profit_per_yr']) + ' a year</b>, with no cash of ours tied up. Nothing else '
         'we carry comes close.'),
     Spacer(1,7),
     grid([['','Memo stones'],
       ['Stones taken in since 2021','{:,.0f}  (about {:,.0f} a year)'.format(M['taken'],M['taken_per_yr'])],
       ['Stones sold','{:,.0f}'.format(M['sold'])],
       ['Profit made', m(M['profit_all'])],
       ['Profit per year', m(M['profit_per_yr'])],
       ['Profit per stone sold', m(M['per_stone'])],
       ['Margin','{:.0f}%'.format(M['margin'])],
       ['Half of them sell within','{:.0f} days'.format(M['med_days_all'])],
       ['Sold within 90 days','{:.0f}%'.format(M['pct_90'])],
       ['Sold within 6 months','{:.0f}%'.format(M['pct_6m'])]],
      [3.0*inch,4.0*inch], cols=False,
      extra=[('BACKGROUND',(0,3),(-1,3),GREEN),('ALIGN',(1,0),(-1,-1),'LEFT')]),
     Spacer(1,9),
     Paragraph('<b>Two different things are happening inside that number</b>', ParagraphStyle('mh',
               parent=NOTE,fontSize=10,textColor=NAVY,spaceAfter=4)),
     grid([['','Already sold before it arrived','Put in the case on spec'],
       ['Stones','{:,.0f}   ({:.0f}%)'.format(M['n_pre'],M['pct_pre']),
                 '{:,.0f}   ({:.0f}%)'.format(M['n_spec'],M['pct_spec'])],
       ['Typical days to sell','{:.0f} days'.format(M['med_days_pre']),'{:.0f} days'.format(M['med_days_spec'])],
       ['Profit made', m(M['profit_pre']), m(M['profit_spec'])]],
      [2.2*inch,2.4*inch,2.4*inch]),
     Spacer(1,5),
     Paragraph('Nearly three in ten are ordered against a customer already standing in the store and are gone '
               'in about ' + '{:.0f}'.format(M['med_days_pre']) + ' days, so they carry no risk at all. The '
               'other seven in ten go into the case on spec and still clear in ' +
               '{:.0f}'.format(M['med_days_spec']) + ' days, faster than any line we actually pay for. The '
               'inventory record shows ' + '{:,.0f}'.format(M['unsold']) + ' stones with no sale against them, '
               'but on memo that mixes stones still on the floor with stones already sent back to the vendor, '
               'so read it as an upper bound rather than as current stock.', NOTE)))

    # ---------------- PAGE 3 ----------------
    story.append(PageBreak())
    rows=[['Category','Profit per $1\nin 6 months','Profit made\nsince 2021','Profit per\npiece sold',
           'Typical\ndays','Margin']]
    cit_row=None
    for i,r in c.iterrows():
        rows.append([r['label'],'{:.0f}¢'.format(round(r['ret6m']*100)),m(r['profit_total']),
                     m(r['profit_per_piece']),'{:.0f}'.format(r['med_days']),'{:.0f}%'.format(r['margin'])])
        if r['category']=='Citizen watches': cit_row=len(rows)-1
    story.append(sec(
     Paragraph('EVERY CATEGORY, RANKED BY SIX MONTH RETURN', HEAD),
     Paragraph('<b>Profit per $1 in 6 months</b> is what a dollar of stock hands back as profit within six months '
               'of arriving. <b>Profit made since 2021</b> is the real money the category has actually returned '
               'over that period. Both count speculative stock only, so special orders bought against a waiting '
               'customer are excluded.', NOTE),
     Spacer(1,5),
     grid(rows,[1.95*inch,1.0*inch,1.1*inch,.95*inch,.85*inch,.75*inch],fs=9,hfs=8,
          extra=[('BACKGROUND',(0,cit_row),(-1,cit_row),colors.HexColor('#FBE9E9'))])))

    # ---------------- PAGE 3 ----------------
    story.append(PageBreak())
    story.append(sec(
     Paragraph('WHAT I WOULD DO', HEAD),
     box('<b>1. Stop buying Citizen.</b> Last of ' + str(NCAT) + ' on return, slowest to turn at 315 days, and '
         'half of what is left has been in the case over two years. In five and a half years the whole category '
         'has made ' + m(CT['profit_total']) + ', about ' + m(CT['profit_per_piece']) + ' a watch.<br/><br/>'
         '<b>2. Buy lab anniversary bands until they stop selling.</b> Best return of anything we pay for at '
         '50&#162; on the dollar and a 92 day turn, but we have only ever bought 65 of them. We are '
         'understocking this line and will not know its ceiling until we push it.<br/><br/>'
         '<b>3. Keep lab center engagement rings well stocked.</b> 40&#162; per dollar, $2,322 average ticket, '
         + m(LC['profit_total']) + ' made on ' + str(int(LC['units_sold'])) + ' pieces sold. The best finished '
         'goods category we have.<br/><br/>'
         '<b>4. Lab accent rings are the next line down.</b> 33&#162; per dollar and a 165 day turn, '
         + m(AC['profit_total']) + ' made on ' + str(int(AC['units_sold'])) + ' pieces. Worth stocking, but '
         'behind the two above it.<br/><br/>'
         '<b>5. Keep taking loose lab grown diamonds on memo.</b> ' + m(M['profit_all']) + ' made since 2021 with '
         'no cash of ours in them, the fastest turn in the store, and the highest margin. See page 2.',
         fill=colors.HexColor('#EEF3FA'), border=NAVY)))

    story.append(Paragraph('HOW THIS WAS BUILT, AND WHERE IT COULD BE WRONG', HEAD))
    for h,t in [
     ('What counts as a lab center ring',
      'A ring whose center stone is a lab grown diamond, not one merely set with lab melee. It qualifies on a single lab '
      'stone named with a carat weight, a lab certificate (GIA/IGI/GCAL), "solitaire" or "center" beside a lab stone, or a '
      'loose lab diamond SKU set into the mounting. All 41 referenced stones were checked individually and every one is a '
      'genuine lab grown diamond. 97 pieces qualify.'),
     ('What was left out',
      '206 rings set only with lab melee (halos, pav&eacute;, eternity bands), a separate and slower group. Lab sapphires, '
      'emeralds and rubies are not diamonds; moissanite centers are not either. 136 vendor ***SAMPLE*** pieces are memo '
      'display items that were never ours to sell. Other brand watches are excluded at your request. All left out.'),
     ('Presold merchandise is excluded from the category table',
      'Nearly three in ten loose lab diamonds sell within two weeks of arriving, bought against a customer already in the '
      'store rather than stocked. Counting those would have shown 49&#162; instead of 38&#162; for that line. Every figure '
      'in the category table prices genuinely speculative inventory only, which is why its profit totals run a little '
      'below the all sales figures on page 1.'),
     ('Small categories, small samples',
      'Lab anniversary bands rest on 65 pieces and lab center rings on 88. They are the two most promising lines here and '
      'the two thinnest samples. Treat the first orders as a test rather than a commitment, and watch how they sell.'),
     ('What the profit figures do and do not include',
      'Merchandise gross profit only: what we sold it for less what we paid for it. No labour, rent, financing or markdown '
      'assumption. Every figure is Orem store POS data as at 19 August 2026.')]:
        story.append(Paragraph('<b>' + h + '</b>', ParagraphStyle('h4',parent=NOTE,fontSize=10,
                     textColor=NAVY,spaceBefore=9,spaceAfter=2)))
        story.append(Paragraph(t, NOTE))
    return story

def make(path,total):
    def page(cv,doc):
        cv.saveState()
        cv.setFont('Helvetica',8); cv.setFillColor(colors.HexColor('#777777'))
        cv.drawString(.75*inch,10.55*inch,'Sierra West Jewelers  •  Merchandise Return Report')
        cv.drawRightString(7.75*inch,10.55*inch,'Page %d of %s'%(cv.getPageNumber(),total))
        cv.setStrokeColor(colors.HexColor('#CCCCCC')); cv.setLineWidth(.6)
        cv.line(.75*inch,10.45*inch,7.75*inch,10.45*inch)
        cv.restoreState()
    doc=BaseDocTemplate(path,pagesize=letter,leftMargin=.75*inch,rightMargin=.75*inch,
        topMargin=.85*inch,bottomMargin=.7*inch,title='Sierra West Jewelers Merchandise Return Report')
    doc.addPageTemplates([PageTemplate(id='p',frames=[Frame(.75*inch,.7*inch,W,9.6*inch,
        leftPadding=0,rightPadding=0,topPadding=0,bottomPadding=0)],onPage=page)])
    doc.build(build_story())
    return doc.page

buf=io.BytesIO(); n=make(buf,'?')          # pass one: how many pages
make('Sierra_West_Merchandise_Report.pdf',n)
print('built %d pages, %d categories'%(n,NCAT))
