# Verify_Import.ps1 - READ ONLY. Full post-import audit of the Clarity databases.
#
# Opens every database with Mode=Read and issues SELECTs only. It never writes,
# never repairs, never changes a byte. Safe on live files.
#
# Part A audits PRIMARY: did the import land correctly, and is anything about it
#        going to cause an error in Clarity.
# Part B profiles SECONDARY: what it is, how its schema compares to primary, and
#        what is needed to feed it the same GP data.
#
# Writes GP_Verify_Report.txt next to this script. Send that file back.
#
# Same folder rules as the import: looks only in its own folder, at
# <store>\primary.accdb, or at primary.accdb here with  -Store 1ORM

param([string]$Store, [switch]$Auto)

$ErrorActionPreference = 'Continue'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Add-Type -AssemblyName System.Data

$report = Join-Path $here 'GP_Verify_Report.txt'
if (Test-Path $report) { Remove-Item $report -Force }
function W($msg, $color='Gray') {
    Write-Host $msg -ForegroundColor $color
    Add-Content -Path $report -Value $msg
}
function Hdr($t) { W ''; W ('=' * 72); W $t; W ('=' * 72) }
function Sub($t) { W ''; W ("-- $t " + ('-' * [Math]::Max(0, 68 - $t.Length))) }

$BASE   = 900000
$TABLES = @('Customers','Customer_PastHistory','POS_SalesSlip','POS_Transactions')
$ALL_STORES = @('1ORM','2SDY','3MRY')
$script:problems = @()
$script:warnings = @()
function Bad($m)  { $script:problems += $m; W ("   *** PROBLEM: $m") 'Red' }
function Warn($m) { $script:warnings += $m; W ("   !   note:   $m") 'Yellow' }
function Good($m) { W ("   ok       $m") 'Green' }

function Open-Read($dbPath) {
    foreach ($prov in 'Microsoft.ACE.OLEDB.16.0','Microsoft.ACE.OLEDB.12.0') {
        try {
            $c = New-Object System.Data.OleDb.OleDbConnection("Provider=$prov;Data Source=$dbPath;Mode=Read;")
            $c.Open(); return $c
        } catch { }
    }
    return $null
}
# scalar query; returns $null and records nothing if the query cannot run
function Q($conn, $sql) {
    try {
        $cmd = $conn.CreateCommand(); $cmd.CommandText = $sql
        $v = $cmd.ExecuteScalar()
        if ($v -is [System.DBNull]) { return $null }
        return $v
    } catch { return '?' }
}
function QN($conn, $sql) {   # numeric, missing -> 0
    $v = Q $conn $sql
    if ($null -eq $v -or $v -eq '?') { return 0 }
    return [double]$v
}
function Fmt($n) { if ($null -eq $n) { return '(null)' }; if ($n -eq '?') { return '(query failed)' }; return ('{0:N0}' -f [double]$n) }
function Money($n) { if ($null -eq $n -or $n -eq '?') { return '-' }; return ('${0:N2}' -f [double]$n) }

# table list with LOCAL vs LINKED, straight from the provider
function Get-TableTypes($conn) {
    $out = @{}
    try {
        $g = [System.Data.OleDb.OleDbSchemaGuid]::Tables
        $t = $conn.GetOleDbSchemaTable($g, $null)
        foreach ($r in $t.Rows) {
            $tt = "$($r['TABLE_TYPE'])"
            $nm = "$($r['TABLE_NAME'])"
            if ($nm -like 'MSys*') { continue }
            $out[$nm] = $tt
        }
    } catch { }
    return $out
}
function Get-Columns($conn, $tbl) {
    $out = @()
    try {
        $g = [System.Data.OleDb.OleDbSchemaGuid]::Columns
        $t = $conn.GetOleDbSchemaTable($g, @($null, $null, $tbl, $null))
        $rows = @($t.Rows) | Sort-Object { [int]$_['ORDINAL_POSITION'] }
        foreach ($r in $rows) { $out += ("$($r['COLUMN_NAME'])" + ':' + "$($r['DATA_TYPE'])") }
    } catch { }
    return $out
}

# ---------------- locate ----------------
$sets = @()
foreach ($s in $ALL_STORES) {
    if (Test-Path (Join-Path $here "$s\primary.accdb")) { $sets += @{ Store = $s; dir = (Join-Path $here $s) } }
}
if ($sets.Count -eq 0 -and (Test-Path (Join-Path $here 'primary.accdb'))) {
    if (-not $Store) { $Store = '(this folder)' }
    $sets += @{ Store = $Store; dir = $here }
}
if ($sets.Count -eq 0) { Write-Host 'No primary.accdb found in this folder.' -ForegroundColor Red; if (-not $Auto) { Read-Host 'Enter' }; exit 1 }

Write-Host 'Reading... on databases this size the join checks can take several minutes.' -ForegroundColor Cyan
W "GP -> Clarity import verification"
W "run $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  on $env:COMPUTERNAME  by $env:USERNAME"
W "folder: $here"

foreach ($x in $sets) {
    $st  = $x.Store
    $pri = Join-Path $x.dir 'primary.accdb'
    $sec = Join-Path $x.dir 'secondary.accdb'

    Hdr "STORE $st"

    # ---------- file health ----------
    Sub 'file health'
    foreach ($pair in @(@('primary',$pri), @('secondary',$sec))) {
        $lbl = $pair[0]; $pth = $pair[1]
        if (-not (Test-Path $pth)) { W ("   {0,-10} (not present)" -f $lbl); continue }
        $fi = Get-Item $pth
        $gb = $fi.Length / 1GB
        W ("   {0,-10} {1,7:N2} GB   modified {2}" -f $lbl, $gb, $fi.LastWriteTime)
        if ($gb -ge 1.90) { Bad "$lbl is $([math]::Round($gb,2)) GB - Access hard-fails at 2 GB. Compact & Repair before this file is used." }
        elseif ($gb -ge 1.70) { Warn "$lbl is $([math]::Round($gb,2)) GB - closing on the 2 GB Access limit. Compact & Repair soon." }
        elseif ($gb -ge 1.40) { Warn "$lbl is $([math]::Round($gb,2)) GB - worth a Compact & Repair to reclaim insert bloat." }
    }
    $bk = @(Get-ChildItem -Path $x.dir -Filter 'primary.backup_*.accdb' -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending)
    if ($bk.Count -gt 0) { W ("   backup     {0,7:N2} GB   {1}  ({2})" -f ($bk[0].Length/1GB), $bk[0].LastWriteTime, $bk[0].Name) }
    else { Warn 'no pre-import backup found in this folder' }

    $cP = Open-Read $pri
    if (-not $cP) { Bad "cannot open $pri (ACE engine missing, or file in use exclusively)"; continue }

    # ---------- local vs linked ----------
    Sub 'tables: local or linked'
    $tt = Get-TableTypes $cP
    foreach ($t in $TABLES) {
        if ($tt.ContainsKey($t)) {
            $kind = $tt[$t]
            W ("   {0,-22} {1}" -f $t, $kind)
            if ($kind -match 'LINK') { Bad "$t in primary is a LINKED table - the import wrote through it to another file, not into primary.accdb" }
        } else { W ("   {0,-22} (absent)" -f $t) }
    }

    # ---------- wildcard sanity: this gates every dedup check below ----------
    Sub 'marker lookup (how the import recognises its own rows)'
    $byId   = QN $cP "SELECT COUNT(*) FROM POS_SalesSlip WHERE ID >= $BASE"
    $byPct  = QN $cP "SELECT COUNT(*) FROM POS_SalesSlip WHERE Notes LIKE 'GPIMPORT:%'"
    $byStar = QN $cP "SELECT COUNT(*) FROM POS_SalesSlip WHERE Notes LIKE 'GPIMPORT:*'"
    W ("   slips with ID >= {0}        {1}" -f $BASE, (Fmt $byId))
    W ("   Notes LIKE 'GPIMPORT:%'      {0}" -f (Fmt $byPct))
    W ("   Notes LIKE 'GPIMPORT:*'      {0}" -f (Fmt $byStar))
    if ($byId -gt 0 -and $byPct -eq 0 -and $byStar -gt 0) {
        Bad "this engine wants * not % as the LIKE wildcard. The import's duplicate check silently matched nothing - re-running it would have duplicated every slip. Check the duplicate counts below closely."
    } elseif ($byId -gt 0 -and $byPct -eq 0) {
        Bad "no slip carries a GPIMPORT marker although $([int]$byId) slips sit in the imported ID range - dedup on a re-run cannot work."
    } elseif ($byPct -gt 0 -and [math]::Abs($byPct - $byId) -gt 0) {
        Warn "marker count ($([int]$byPct)) and ID-range count ($([int]$byId)) disagree by $([math]::Abs($byPct-$byId))"
    } else { Good 'markers and ID range agree' }
    $mk = "Notes LIKE 'GPIMPORT:%'"
    if ($byPct -eq 0 -and $byStar -gt 0) { $mk = "Notes LIKE 'GPIMPORT:*'" }

    # ---------- what landed ----------
    Sub 'what landed'
    $custAll = QN $cP 'SELECT COUNT(*) FROM Customers'
    $custGP  = QN $cP "SELECT COUNT(*) FROM Customers WHERE [Source] = 'GP'"
    $slipAll = QN $cP 'SELECT COUNT(*) FROM POS_SalesSlip'
    $slipGP  = QN $cP "SELECT COUNT(*) FROM POS_SalesSlip WHERE $mk"
    $txAll   = QN $cP 'SELECT COUNT(*) FROM POS_Transactions'
    $txGP    = QN $cP "SELECT COUNT(*) FROM POS_Transactions WHERE SalesSlipID >= $BASE"
    W ("   customers total {0,12}   from GP {1,10}" -f (Fmt $custAll), (Fmt $custGP))
    W ("   slips     total {0,12}   from GP {1,10}" -f (Fmt $slipAll), (Fmt $slipGP))
    W ("   lines     total {0,12}   from GP {1,10}" -f (Fmt $txAll),  (Fmt $txGP))
    if ($tt.ContainsKey('Customer_PastHistory')) {
        $noteAll = QN $cP 'SELECT COUNT(*) FROM Customer_PastHistory'
        $noteGP  = QN $cP "SELECT COUNT(*) FROM Customer_PastHistory WHERE PastHistory LIKE '%Great Plains history%'"
        if ($noteGP -eq 0) { $noteGP = QN $cP "SELECT COUNT(*) FROM Customer_PastHistory WHERE PastHistory LIKE '*Great Plains history*'" }
        W ("   notes     total {0,12}   from GP {1,10}" -f (Fmt $noteAll), (Fmt $noteGP))
    }

    # ---------- duplicates ----------
    Sub 'duplicates (must all be zero)'
    $dupMarker = QN $cP "SELECT COUNT(*) FROM (SELECT Notes FROM POS_SalesSlip WHERE $mk GROUP BY Notes HAVING COUNT(*) > 1) AS d"
    $dupSlip   = QN $cP "SELECT COUNT(*) FROM (SELECT ID FROM POS_SalesSlip GROUP BY ID HAVING COUNT(*) > 1) AS d"
    $dupTx     = QN $cP "SELECT COUNT(*) FROM (SELECT TransactionID FROM POS_Transactions GROUP BY TransactionID HAVING COUNT(*) > 1) AS d"
    $dupCust   = QN $cP "SELECT COUNT(*) FROM (SELECT ID FROM Customers GROUP BY ID HAVING COUNT(*) > 1) AS d"
    foreach ($p in @(@('GP invoices imported twice',$dupMarker), @('duplicate slip IDs',$dupSlip),
                     @('duplicate transaction IDs',$dupTx), @('duplicate customer IDs',$dupCust))) {
        W ("   {0,-32} {1}" -f $p[0], (Fmt $p[1]))
        if ($p[1] -gt 0) { Bad "$($p[0]): $([int]$p[1])" }
    }
    if ($dupMarker -eq 0 -and $dupSlip -eq 0 -and $dupTx -eq 0 -and $dupCust -eq 0) { Good 'no duplicates anywhere' }

    # ---------- orphans / broken links ----------
    Sub 'orphans (must all be zero)'
    $slipNoLines = QN $cP ("SELECT COUNT(*) FROM POS_SalesSlip AS s LEFT JOIN " +
        "(SELECT DISTINCT SalesSlipID FROM POS_Transactions WHERE SalesSlipID >= $BASE) AS t " +
        "ON s.ID = t.SalesSlipID WHERE s.ID >= $BASE AND t.SalesSlipID IS NULL")
    $lineNoSlip  = QN $cP "SELECT COUNT(*) FROM POS_Transactions AS t LEFT JOIN POS_SalesSlip AS s ON t.SalesSlipID = s.ID WHERE t.SalesSlipID >= $BASE AND s.ID IS NULL"
    $slipNoCust  = QN $cP "SELECT COUNT(*) FROM POS_SalesSlip AS s LEFT JOIN Customers AS c ON s.CustID = c.ID WHERE s.ID >= $BASE AND c.ID IS NULL"
    foreach ($p in @(@('imported slips with no lines',$slipNoLines), @('imported lines with no slip',$lineNoSlip),
                     @('imported slips with no customer',$slipNoCust))) {
        W ("   {0,-32} {1}" -f $p[0], (Fmt $p[1]))
        if ($p[1] -gt 0) { Bad "$($p[0]): $([int]$p[1]) - these show up broken in Clarity" }
    }
    if ($tt.ContainsKey('Customer_PastHistory')) {
        $noteNoCust = QN $cP "SELECT COUNT(*) FROM Customer_PastHistory AS n LEFT JOIN Customers AS c ON n.CustID = c.ID WHERE c.ID IS NULL"
        W ("   {0,-32} {1}" -f 'notes with no customer', (Fmt $noteNoCust))
        if ($noteNoCust -gt 0) { Bad "purchase-history notes pointing at customers that do not exist: $([int]$noteNoCust)" }
    }
    if ($slipNoLines -eq 0 -and $lineNoSlip -eq 0 -and $slipNoCust -eq 0) { Good 'every imported slip has its lines and its customer' }

    # ---------- money ----------
    Sub 'money'
    $sumSlip = QN $cP "SELECT SUM(TotalSale) FROM POS_SalesSlip WHERE $mk"
    $sumLine = QN $cP "SELECT SUM(TransactionPrice) FROM POS_Transactions WHERE SalesSlipID >= $BASE"
    $maxSale = QN $cP "SELECT MAX(TotalSale) FROM POS_SalesSlip WHERE $mk"
    $negSale = QN $cP "SELECT COUNT(*) FROM POS_SalesSlip WHERE $mk AND TotalSale < 0"
    $zeroSale= QN $cP "SELECT COUNT(*) FROM POS_SalesSlip WHERE $mk AND TotalSale = 0"
    $huge    = QN $cP "SELECT COUNT(*) FROM POS_SalesSlip WHERE $mk AND TotalSale > 500000"
    W ("   slip totals add to        {0}" -f (Money $sumSlip))
    W ("   line amounts add to       {0}" -f (Money $sumLine))
    W ("   largest single slip       {0}" -f (Money $maxSale))
    W ("   slips at zero             {0}" -f (Fmt $zeroSale))
    W ("   slips negative            {0}" -f (Fmt $negSale))
    W ("   slips over `$500,000       {0}" -f (Fmt $huge))
    if ($sumSlip -ne 0 -and [math]::Abs($sumSlip - $sumLine) -gt 1) {
        Bad ("slip totals and line totals differ by " + (Money ([math]::Abs($sumSlip - $sumLine))) + " - a slip header does not match its lines")
    } else { Good 'slip headers and their lines agree to the cent' }
    if ($huge -gt 0) { Warn "$([int]$huge) slips over `$500,000 - check these are real and not a decimal-place decode error" }
    if ($negSale -gt 0) { Warn "$([int]$negSale) negative slips (returns are plausible, but confirm)" }

    # ---------- dates ----------
    Sub 'dates'
    $minD = Q $cP "SELECT MIN([TimeStamp]) FROM POS_SalesSlip WHERE $mk"
    $maxD = Q $cP "SELECT MAX([TimeStamp]) FROM POS_SalesSlip WHERE $mk"
    $future = QN $cP "SELECT COUNT(*) FROM POS_SalesSlip WHERE $mk AND [TimeStamp] > Now()"
    $zeroD  = QN $cP "SELECT COUNT(*) FROM POS_SalesSlip WHERE $mk AND [TimeStamp] < #1900-01-01#"
    W ("   earliest imported sale    {0}" -f $minD)
    W ("   latest imported sale      {0}" -f $maxD)
    W ("   dated in the future       {0}" -f (Fmt $future))
    W ("   dated before 1900         {0}" -f (Fmt $zeroD))
    if ($future -gt 0) { Bad "$([int]$future) imported slips are dated in the future" }
    if ($zeroD -gt 0)  { Bad "$([int]$zeroD) imported slips have a broken/zero date" }
    if ($future -eq 0 -and $zeroD -eq 0) { Good 'all imported dates are sane' }

    # ---------- ID space / collision risk ----------
    Sub 'ID numbering'
    $maxCust    = QN $cP 'SELECT MAX(ID) FROM Customers'
    $maxCustReal= QN $cP "SELECT MAX(ID) FROM Customers WHERE [Source] <> 'GP' OR [Source] IS NULL"
    $minCustGP  = QN $cP "SELECT MAX(ID) FROM Customers WHERE [Source] = 'GP'"
    $minGPid    = QN $cP "SELECT MIN(ID) FROM Customers WHERE [Source] = 'GP'"
    $maxSlipId  = QN $cP 'SELECT MAX(ID) FROM POS_SalesSlip'
    $slipBelow  = QN $cP "SELECT COUNT(*) FROM POS_SalesSlip WHERE $mk AND ID < $BASE"
    W ("   highest customer ID overall      {0}" -f (Fmt $maxCust))
    W ("   highest NON-GP customer ID       {0}" -f (Fmt $maxCustReal))
    W ("   GP customer IDs run              {0} .. {1}" -f (Fmt $minGPid), (Fmt $minCustGP))
    W ("   free numbers before the GP block {0}" -f (Fmt ($minGPid - $maxCustReal - 1)))
    W ("   highest slip ID                  {0}" -f (Fmt $maxSlipId))
    W ("   imported slips below {0}      {1}" -f $BASE, (Fmt $slipBelow))
    if ($slipBelow -gt 0) { Bad "$([int]$slipBelow) imported slips sit below the $BASE block - they can collide with real POS slips" }
    $gap = $minGPid - $maxCustReal - 1
    if ($gap -gt 0 -and $gap -lt 2000) {
        Warn "only $([int]$gap) customer numbers are free between the live data and the GP block. If Clarity issues IDs from its own counter rather than MAX+1, it will collide after about that many new customers. Add one test customer in Clarity and check the ID it gets."
    }

    # ---------- fields Clarity shows ----------
    Sub 'fields Clarity displays for imported customers'
    $noSpend = QN $cP "SELECT COUNT(*) FROM Customers WHERE [Source] = 'GP' AND (TotalSpent IS NULL OR TotalSpent = 0)"
    $noHome  = QN $cP "SELECT COUNT(*) FROM Customers WHERE [Source] = 'GP' AND (home_store IS NULL OR home_store = '')"
    $noLast  = QN $cP "SELECT COUNT(*) FROM Customers WHERE [Source] = 'GP' AND LastPurchase IS NULL"
    W ("   GP customers showing `$0 TotalSpent   {0}" -f (Fmt $noSpend))
    W ("   GP customers with no home_store      {0}" -f (Fmt $noHome))
    W ("   GP customers with no LastPurchase    {0}" -f (Fmt $noLast))
    if ($noSpend -gt 0) { Warn "$([int]$noSpend) imported customers will read as `$0 lifetime spend at POS even though their slips are attached" }

    $cP.Close()

    # =============== PART B: secondary ===============
    Sub 'SECONDARY'
    if (-not (Test-Path $sec)) { W '   secondary.accdb is not present in this folder.'; continue }
    $cS = Open-Read $sec
    if (-not $cS) { Bad "cannot open $sec"; continue }

    $ttS = Get-TableTypes $cS
    W '   tables in secondary:'
    foreach ($t in $TABLES) {
        if ($ttS.ContainsKey($t)) { W ("     {0,-22} {1}" -f $t, $ttS[$t]) }
        else { W ("     {0,-22} ABSENT" -f $t) }
    }
    W '   row counts, secondary vs primary:'
    $cP2 = Open-Read $pri
    foreach ($t in $TABLES) {
        if (-not $ttS.ContainsKey($t)) { continue }
        $ns = QN $cS "SELECT COUNT(*) FROM [$t]"
        $np = if ($cP2) { QN $cP2 "SELECT COUNT(*) FROM [$t]" } else { 0 }
        $flag = if ($ns -eq $np) { 'same' } else { ('behind by ' + ('{0:N0}' -f ($np - $ns))) }
        W ("     {0,-22} secondary {1,12}   primary {2,12}   {3}" -f $t, (Fmt $ns), (Fmt $np), $flag)
    }
    $secGP = QN $cS "SELECT COUNT(*) FROM Customers WHERE [Source] = 'GP'"
    W ("   GP customers already in secondary: {0}" -f (Fmt $secGP))

    W '   schema differences that would break a write into secondary:'
    $anyDiff = $false
    foreach ($t in $TABLES) {
        if (-not $ttS.ContainsKey($t)) { continue }
        $colP = Get-Columns $cP2 $t
        $colS = Get-Columns $cS  $t
        $onlyP = @($colP | Where-Object { $colS -notcontains $_ })
        $onlyS = @($colS | Where-Object { $colP -notcontains $_ })
        if ($onlyP.Count -eq 0 -and $onlyS.Count -eq 0) {
            W ("     {0,-22} identical ({1} columns)" -f $t, $colP.Count)
        } else {
            $anyDiff = $true
            W ("     {0,-22} DIFFERENT" -f $t) 
            if ($onlyP.Count -gt 0) { W ("        only in primary  : " + ($onlyP -join ', ')) }
            if ($onlyS.Count -gt 0) { W ("        only in secondary: " + ($onlyS -join ', ')) }
        }
    }
    if (-not $anyDiff) { Good 'secondary schema matches primary - the same INSERTs will work against it' }
    else { Warn 'secondary schema differs from primary - the import INSERT lists need adjusting before writing to it' }
    if ($cP2) { $cP2.Close() }
    $cS.Close()
}

Hdr 'SUMMARY'
if ($script:problems.Count -eq 0) { W 'No problems found.' 'Green' }
else {
    W ("PROBLEMS ({0}) - do not put these files back until these are resolved:" -f $script:problems.Count) 'Red'
    foreach ($p in $script:problems) { W "  * $p" 'Red' }
}
if ($script:warnings.Count -gt 0) {
    W ''
    W ("Notes ({0}):" -f $script:warnings.Count) 'Yellow'
    foreach ($p in $script:warnings) { W "  ! $p" 'Yellow' }
}
W ''
W "Report written to: $report"
if (-not $Auto) { Read-Host 'Press Enter to close' }
