#!/usr/bin/env python3
"""Extract customers + sales history from Great Plains Dynamics (Btrieve 6.x) data files.

Decoding notes (established empirically against SWD company data):
- Strings are stored as [width byte][chars padded with spaces to width].
- Dates are 4 bytes: day, month, year as little-endian word.
- Currency is 10-byte packed BCD, last nibble C=positive D=negative, 5 implied decimals
  (verified against duplicate 2006-07 transactions recorded in both GP and the Clarity POS).
"""
import re, csv, struct, os

SWD = 'gp_data/DSW_GreatPlains/dynamics/SWD'
OUT = 'gp_out'
os.makedirs(OUT, exist_ok=True)

def bcd(cell):
    """10-byte packed decimal -> float (4 implied decimals)."""
    digits = ''
    for b in cell[:-1]:
        digits += f'{b >> 4}{b & 0xF}'
    digits += str(cell[-1] >> 4)
    sign = cell[-1] & 0xF
    try:
        v = int(digits) / 100000.0
    except ValueError:
        return None
    return -v if sign == 0xD else v

def date4(b):
    if len(b) < 4: return ''
    d, m, y = b[0], b[1], struct.unpack('<H', b[2:4])[0]
    if 1900 < y < 2100 and 1 <= m <= 12 and 1 <= d <= 31:
        return f'{y:04d}-{m:02d}-{d:02d}'
    return ''

def s(raw):
    return raw.decode('latin1').strip()

# ---------------- RM00101: customer master ----------------
def customers():
    data = open(f'{SWD}/sales/RM00101.btr','rb').read()
    pat = re.compile(rb'\x0f([\x20-\x7e]{15})\x41([\x20-\x7e]{65})')
    rows = {}
    for m in pat.finditer(data):
        pos = m.end()
        custnmbr, custname = s(m.group(1)), s(m.group(2))
        if not custnmbr or not custname: continue
        # walk remaining string fields
        fields = []
        p = pos
        while len(fields) < 26 and p < len(data)-1:
            n = data[p]
            if n == 0 or n > 127: break
            chunk = data[p+1:p+1+n]
            if sum(1 for b in chunk if 32 <= b < 127) < n * 0.95: break
            fields.append(chunk.decode('latin1'))
            p += 1 + n
        def g(i): return s(fields[i].encode('latin1')) if i < len(fields) else ''
        phone = g(16)
        phone = phone[:10] if phone and phone.strip('0') else ''
        rows[custnmbr] = dict(
            custnmbr=custnmbr, name=custname, custclass=g(0),
            address1=g(9), city=g(13), state=g(14), zip=g(15),
            phone1=phone, salesperson=g(23))
    with open(f'{OUT}/customers.csv','w',newline='') as f:
        w = csv.DictWriter(f, rows[next(iter(rows))].keys()); w.writeheader()
        for r in rows.values(): w.writerow(r)
    return len(rows)

# ---------------- RM00301: salesperson master ----------------
def salespeople():
    path = f'{SWD}/sales/RM00301.btr'
    data = open(path,'rb').read()
    pat = re.compile(rb'\x0f([\x20-\x7e]{15})\x0f[\x20-\x7e]{15}\x0f[\x20-\x7e]{15}\x0f([\x20-\x7e]{15})\x0f[\x20-\x7e]{15}\x15([\x20-\x7e]{21})')
    rows = {}
    for m in pat.finditer(data):
        a, first, last = s(m.group(1)), s(m.group(2)), s(m.group(3))
        if a:
            rows.setdefault(a, (first, last))
    with open(f'{OUT}/salespeople.csv','w',newline='') as f:
        w = csv.writer(f); w.writerow(['slprsnid','firstname','lastname'])
        for k,(x,y) in rows.items(): w.writerow([k,x,y])
    return len(rows)

# ---------------- SOP30200: sales headers (one record per 2048-byte page) ----
def headers():
    data = open(f'{SWD}/sales/SOP30200.BTR','rb').read()
    PS = 2048
    rec_pat = re.compile(rb'([\x01-\x04])\x00\x15([\x20-\x7e]{21})\x00{2}\x15([\x20-\x7e]{21})\x0f([\x20-\x7e]{15})(.{4})')
    cust_pat = re.compile(rb'\x0f([\x20-\x7e]{15})\x41([\x20-\x7e]{65})')
    seq_pat = re.compile(rb'\x15Cash|\x15[\x20-\x7e]{21}\x0b')
    rows = {}
    for pg in range(len(data)//PS):
        page = data[pg*PS:(pg+1)*PS]
        m = rec_pat.search(page[:80])
        if not m: continue
        soptype = m.group(1)[0]
        sopnumbe = s(m.group(2))
        docid = s(m.group(4))
        docdate = date4(m.group(5))
        if not sopnumbe or not docdate: continue
        # batch + customer: strings after the payment-terms block
        cm = cust_pat.search(page)
        custnmbr = s(cm.group(1)) if cm else ''
        custname = s(cm.group(2)) if cm else ''
        # location + batch: LOCNCODE(11) & BACHNUMB(15) sit just before customer
        loc = batch = ''
        if cm:
            pre = page[:cm.start()]
            lm = re.search(rb'\x0b([\x20-\x7e]{11})\x0f([\x20-\x7e]{15})\x0f([\x20-\x7e]{15})$', pre)
            if lm:
                loc, batch = s(lm.group(1)), s(lm.group(3))
        rows[(soptype, sopnumbe)] = dict(
            soptype=soptype, sopnumbe=sopnumbe, docid=docid, docdate=docdate,
            store=loc, batch=batch, custnmbr=custnmbr, custname=custname)
    with open(f'{OUT}/sales_headers.csv','w',newline='') as f:
        w = csv.DictWriter(f, ['soptype','sopnumbe','docid','docdate','store','batch','custnmbr','custname'])
        w.writeheader()
        for r in rows.values(): w.writerow(r)
    return len(rows)

# ---------------- SOP30300: line items ----------------
def lines():
    data = open(f'{SWD}/sales/SOP30300.btr','rb').read()
    # record: soptype int16, sopnumbe 21, seq bytes(7), itemnmbr 31, itemdesc 51, ... uofm 9, loc 11, currency cells
    pat = re.compile(
        rb'([\x01-\x04])\x00\x15([\x20-\x7e]{21})(.{8})\x1f([\x20-\x7e]{31})\x33([\x20-\x7e]{51})',
        re.S)
    rows = {}
    for m in pat.finditer(data):
        soptype = m.group(1)[0]
        sopnumbe = s(m.group(2))
        seq = struct.unpack('>i', m.group(3)[0:4])[0]
        itemnmbr, itemdesc = s(m.group(4)), s(m.group(5))
        p = m.end()
        tail = data[p:p+400]
        um = re.search(rb'\x09([\x20-\x7e]{9})\x0b([\x20-\x7e]{11})', tail)
        uofm = loc = ''
        cost = price = None
        if um:
            uofm, loc = s(um.group(1)), s(um.group(2))
            q = p + um.end()
            cells = []
            cp = q
            for _ in range(12):
                cell = data[cp:cp+10]
                if len(cell) < 10 or cell[9] & 0xF not in (0xC, 0xD):
                    break
                v = bcd(cell)
                if v is None: break
                cells.append(v)
                cp += 10
            if len(cells) >= 6:
                cost = cells[0]
                price = cells[4]   # XTNDPRC (extended price)
            elif cells:
                cost = cells[0]
                price = cells[-1]
        key = (soptype, sopnumbe, seq, itemnmbr)
        rows[key] = dict(soptype=soptype, sopnumbe=sopnumbe, seq=seq,
                         itemnmbr=itemnmbr, itemdesc=itemdesc, uofm=uofm,
                         store=loc, unitcost=cost, price=price)
    with open(f'{OUT}/sales_lines.csv','w',newline='') as f:
        w = csv.DictWriter(f, ['soptype','sopnumbe','seq','itemnmbr','itemdesc','uofm','store','unitcost','price'])
        w.writeheader()
        for r in rows.values(): w.writerow(r)
    return len(rows)

if __name__ == '__main__':
    print('customers:', customers())
    print('salespeople:', salespeople())
    print('sales headers:', headers())
    print('sales lines:', lines())
