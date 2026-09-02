# GP_ImportAll.ps1 - ONE-SHOT IMPORT into local copies of the Clarity databases.
#
# WHERE IT LOOKS: this script's OWN FOLDER. No searching, no mapped drives, no
#   share. Copy the store databases down next to this script, run it, then copy
#   them back up. Two layouts are accepted:
#
#     A) one folder per store (all stores at once):
#          <this folder>\1ORM\primary.accdb   + secondary.accdb
#          <this folder>\2SDY\primary.accdb   + secondary.accdb
#          <this folder>\3MRY\primary.accdb   + secondary.accdb
#
#     B) a single store's files sitting right here (one store at a time):
#          <this folder>\primary.accdb        + secondary.accdb
#        With layout B you must say which store it is:  -Store 1ORM
#
# PRIMARY + SECONDARY: both files are opened. Which database owns which table is
#   worked out at startup by probing, so it does not matter how Clarity splits
#   them - each write is routed to the file that actually holds that table. If a
#   table lives in both (e.g. primary links to secondary), primary is used.
#
# RUNNING ONE STORE AT A TIME: customer IDs must match across all three stores.
#   Because a per-store run cannot see the other stores, every ID this script
#   hands out is remembered in gp_assigned_ids.csv next to the script. Keep that
#   file - it is what makes store-by-store runs agree with each other.
#
# SELF-HEALING: safe to re-run any time. Finished work is skipped; customers that
#   only made it into some stores get filled into the missing ones; slips that
#   lost their lines mid-write are rebuilt. Re-running never duplicates anything.

param([switch]$Auto, [switch]$NoBackup, [string]$Store)

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Add-Type -AssemblyName System.Data
Add-Type -AssemblyName System.Core   # HashSet on old PowerShell

# PS 2.0-safe blank check ([string]::IsNullOrWhiteSpace needs .NET 4)
function IsBlank($s) { return ($null -eq $s -or ("$s").Trim() -eq '') }
$logFile = Join-Path $here 'GP_Import_log.txt'
function Log($msg, $color='Gray') {
    $line = "$(Get-Date -Format 'MM/dd HH:mm:ss')  $msg"
    Write-Host $line -ForegroundColor $color
    Add-Content -Path $logFile -Value $line
}
function Die($msg) {
    Log "FATAL: $msg  Nothing was changed." 'Red'
    if (-not $Auto) { Read-Host 'Enter to exit' }
    exit 1
}

function Open-Db($dbPath) {
    foreach ($prov in 'Microsoft.ACE.OLEDB.16.0','Microsoft.ACE.OLEDB.12.0') {
        try {
            $c = New-Object System.Data.OleDb.OleDbConnection("Provider=$prov;Data Source=$dbPath;")
            $c.Open(); return $c
        } catch { }
    }
    throw "Could not open $dbPath - the Microsoft Access engine (ACE) is not installed in this PowerShell's flavor. Install the matching Access Database Engine redistributable."
}
function Add-Params($cmd, $params) {
    foreach ($p in $params) {
        if ($p -is [datetime]) {
            $prm = $cmd.Parameters.Add('?', [System.Data.OleDb.OleDbType]::Date)
            $prm.Value = $p.AddMilliseconds(-$p.Millisecond)
        } elseif ($null -eq $p -or $p -is [System.DBNull]) {
            $prm = $cmd.Parameters.Add('?', [System.Data.OleDb.OleDbType]::VarWChar)
            $prm.Value = [System.DBNull]::Value
        } else {
            [void]$cmd.Parameters.AddWithValue('?', $p)
        }
    }
}
function Exec($conn, $sql, $params) {
    $cmd = $conn.CreateCommand(); $cmd.CommandText = $sql
    $clean = foreach ($p in $params) { if ($p -is [string] -and $p -eq '') { [System.DBNull]::Value } else { $p } }
    Add-Params $cmd $clean
    return $cmd.ExecuteNonQuery()
}
function Scalar($conn, $sql, $params) {
    $cmd = $conn.CreateCommand(); $cmd.CommandText = $sql
    Add-Params $cmd $params
    return $cmd.ExecuteScalar()
}
function Fill-Table($conn, $sql, $params) {
    $cmd = $conn.CreateCommand(); $cmd.CommandText = $sql
    Add-Params $cmd $params
    $da = New-Object System.Data.OleDb.OleDbDataAdapter($cmd)
    $tb = New-Object System.Data.DataTable
    [void]$da.Fill($tb)
    return ,$tb
}
# does this database expose this table (natively or as a linked table)?
function Has-Table($conn, $tbl) {
    try {
        $cmd = $conn.CreateCommand(); $cmd.CommandText = "SELECT TOP 1 * FROM [$tbl]"
        $rd = $cmd.ExecuteReader(); $rd.Close(); return $true
    } catch { return $false }
}

# ---- resilient layer -------------------------------------------------------
# Connections are keyed "STORE|primary" / "STORE|secondary" so any one can be
# replaced on the fly; every reconnect is probe-tested before it is trusted.
$script:conns      = @{}   # conn key -> OleDbConnection
$script:dbPaths    = @{}   # conn key -> file path
$script:probeTable = @{}   # conn key -> a table known to live there
$script:tableHome  = @{}   # "STORE|Table" -> conn key
$script:lockWaits  = 0
$script:reopens    = 0
$script:failed     = $null
$CONN_ERRORS = @('*network access was interrupted*','*connection is closed*','*connection was closed*','*cannot find the input table*')

function Test-Conn($key, $c) {
    $t = $script:probeTable[$key]
    if (IsBlank $t) { return $true }
    return (Has-Table $c $t)
}
function Reconnect($key) {
    try { $script:conns[$key].Close() } catch {}
    $p = $script:dbPaths[$key]
    for ($a = 1; $a -le 10; $a++) {
        try {
            $c = Open-Db $p
            if (Test-Conn $key $c) {
                $script:conns[$key] = $c
                Log "  [$key] reopened (verified)" 'Green'
                return
            }
            try { $c.Close() } catch {}
        } catch { }
        if ($a -eq 1) { Log "  [$key] could not reopen yet - retrying every 10s..." 'Yellow' }
        Start-Sleep -Seconds 10
    }
    throw "Could not reopen $p after 10 tries - giving up."
}
function Invoke-DbOp($key, $op, $sql, $params) {
    for ($t = 1; $t -le 8; $t++) {
        try {
            $c = $script:conns[$key]
            if ($op -eq 'exec')       { return Exec $c $sql $params }
            elseif ($op -eq 'scalar') { return Scalar $c $sql $params }
            else                      { return Fill-Table $c $sql $params }
        } catch {
            $m = $_.Exception.Message
            if ($t -eq 8) { throw }
            $isConn = $false
            foreach ($pat in $CONN_ERRORS) { if ($m -like $pat) { $isConn = $true; break } }
            if ($isConn) {
                $script:reopens++
                Log "  [$key] connection unhealthy ($($script:reopens)) - reopening..." 'Yellow'
                Reconnect $key
            } elseif ($m -like '*currently locked*') {
                $script:lockWaits++
                Start-Sleep -Seconds ([Math]::Min(60, 5 * $t))
            } else { throw }
        }
    }
}
# every statement below touches exactly ONE table, so each call names that table
# and is routed to whichever database file actually holds it.
function Route($store, $table) {
    $k = "$store|$table"
    if (-not $script:tableHome.ContainsKey($k)) { throw "No database holds table [$table] for store $store." }
    return $script:tableHome[$k]
}
function ExecR($store, $table, $sql, $params)   { return Invoke-DbOp (Route $store $table) 'exec'   $sql $params }
function ScalarR($store, $table, $sql, $params) { return Invoke-DbOp (Route $store $table) 'scalar' $sql $params }
function TableR($store, $table, $sql, $params)  { return Invoke-DbOp (Route $store $table) 'table'  $sql $params }

# normalize a name/address value (from CSV or DB) into a match key part
function NK($v) {
    if ($null -eq $v -or $v -is [System.DBNull]) { return '' }
    return "$v".Trim()
}

# Who has this database open right now? (machine names from the .laccdb lock file)
function Get-LockMachines($dbPath) {
    $lk = [System.IO.Path]::ChangeExtension($dbPath, '.laccdb')
    if (-not (Test-Path $lk)) { return @() }
    try {
        $fs = New-Object System.IO.FileStream($lk, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
        $bytes = New-Object byte[] ([int]$fs.Length)
        [void]$fs.Read($bytes, 0, $bytes.Length); $fs.Close()
        $names = @()
        for ($o = 0; $o + 64 -le $bytes.Length; $o += 64) {
            $m = [System.Text.Encoding]::ASCII.GetString($bytes, $o, 32).Trim([char]0, ' ')
            if ($m -and $names -notcontains $m) { $names += $m }
        }
        return $names
    } catch { return @('(in use)') }
}

# ================= locate the databases: THIS FOLDER ONLY ====================
$ALL_STORES = @('1ORM','2SDY','3MRY')
$KINDS      = @('primary','secondary')
# Customer_PastHistory is only written at Orem; the rest are needed everywhere.
$NEED = @{
    '1ORM' = @('Customers','Customer_PastHistory','POS_SalesSlip','POS_Transactions')
    '2SDY' = @('Customers','POS_SalesSlip','POS_Transactions')
    '3MRY' = @('Customers','POS_SalesSlip','POS_Transactions')
}

Write-Host '=== Great Plains -> Clarity import (local copies) ===' -ForegroundColor Cyan
Write-Host "Folder: $here"

$sets = @()   # each: @{ Store=..; primary=<path>; secondary=<path or $null> }

# layout A: one subfolder per store
foreach ($s in $ALL_STORES) {
    $p = Join-Path $here "$s\primary.accdb"
    if (Test-Path $p) {
        $sec = Join-Path $here "$s\secondary.accdb"
        if (-not (Test-Path $sec)) { $sec = $null }
        $sets += @{ Store = $s; primary = $p; secondary = $sec }
    }
}
# layout B: a single store's files sitting directly in this folder
if ($sets.Count -eq 0) {
    $p = Join-Path $here 'primary.accdb'
    if (Test-Path $p) {
        if (IsBlank $Store) {
            Die "found primary.accdb in this folder but not which store it belongs to. Re-run naming the store, e.g.  -Store 1ORM  (choices: $($ALL_STORES -join ', '))."
        }
        if ($ALL_STORES -notcontains $Store) { Die "-Store must be one of: $($ALL_STORES -join ', ')." }
        $sec = Join-Path $here 'secondary.accdb'
        if (-not (Test-Path $sec)) { $sec = $null }
        $sets += @{ Store = $Store; primary = $p; secondary = $sec }
    }
}
if ($sets.Count -eq 0) {
    Die "no primary.accdb found. Copy the store databases into this folder first - either as <store>\primary.accdb (all stores) or as primary.accdb here with -Store <name> (one store)."
}

$STORES = @()
foreach ($x in $sets) { $STORES += $x.Store }

foreach ($f in 'import_append_history.csv','import_new_customers.csv') {
    if (-not (Test-Path (Join-Path $here $f))) { Die "missing data file $f." }
}
foreach ($s in $STORES) {
    if (-not (Test-Path (Join-Path $here "slips_$s.csv"))) { Die "missing data file slips_$s.csv." }
}

Write-Host ''
Write-Host 'Will import into:' -ForegroundColor Yellow
foreach ($x in $sets) {
    Write-Host "  $($x.Store)  primary   -> $($x.primary)"
    if ($x.secondary) { Write-Host "  $($x.Store)  secondary -> $($x.secondary)" }
    else { Write-Host "  $($x.Store)  secondary -> (not present)" -ForegroundColor Yellow }
}
if ($STORES.Count -lt 3) {
    Write-Host ''
    Write-Host "NOTE: only $($STORES -join ', ') present. Customers will be added to these stores now; run the other stores later and they will be filled in with the SAME IDs (kept in gp_assigned_ids.csv)." -ForegroundColor Yellow
}
if (-not $Auto) {
    $go = Read-Host 'Type GO to start (anything else quits)'
    if ($go -ne 'GO') { exit 0 }
}

Log '=== RUN START ===' 'Cyan'
Log "  folder: $here"

# ---- only ONE import may run at a time ---------------------------------------
$sentinel = Join-Path $here 'gp_import.lock'
try {
    $script:runLock = [IO.File]::Open($sentinel, [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
} catch {
    Log 'STOP: another import is already running from this folder (gp_import.lock is held).' 'Red'
    Log 'Find that window and close it, then run again. Nothing was changed.' 'Red'
    if (-not $Auto) { Read-Host 'Enter to exit' }
    exit 1
}

try {
# ---- close anything holding the local copies --------------------------------
foreach ($pn in 'MSACCESS','Clarity','ClarityPOS','ClarityStart') {
    foreach ($proc in @(Get-Process -Name $pn -ErrorAction SilentlyContinue)) {
        Log "  stopping local process $pn (PID $($proc.Id)) - it can hold locks" 'Yellow'
        try { $proc.Kill(); [void]$proc.WaitForExit(5000) } catch { }
    }
}
Start-Sleep -Seconds 2

# ---- prove every file is free before touching anything ----------------------
$held = @()
foreach ($x in $sets) {
    foreach ($k in $KINDS) {
        $p = $x[$k]
        if (-not $p) { continue }
        $ok = $false
        for ($i = 1; $i -le 3; $i++) {
            try {
                $h = [IO.File]::Open($p, [IO.FileMode]::Open, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
                $h.Close(); $ok = $true; break
            } catch { Start-Sleep -Seconds 5 }
        }
        if (-not $ok) { $held += "$($x.Store) $k" }
    }
}
if ($held.Count -gt 0) {
    Log "STOP: still held open by some program: $($held -join ', ')." 'Red'
    foreach ($x in $sets) {
        foreach ($k in $KINDS) {
            if (-not $x[$k]) { continue }
            $who = Get-LockMachines $x[$k]
            if ($who.Count -gt 0) { Log "  $($x.Store) $k open on: $($who -join ', ')" 'Yellow' }
        }
    }
    Log 'Close Access/Clarity on this PC and run again. Nothing was changed.' 'Red'
    throw 'databases not free'
}
Log 'Verified: all database files are free.' 'Green'

# ---- open primary + secondary for every store, and map where tables live ----
foreach ($x in $sets) {
    foreach ($k in $KINDS) {
        $p = $x[$k]
        if (-not $p) { continue }
        $key = "$($x.Store)|$k"
        $script:dbPaths[$key] = $p
        try {
            $script:conns[$key] = Open-Db $p
        } catch {
            Log 'STOP: the Microsoft Access engine (ACE) is not available to this PowerShell.' 'Red'
            Log '  Install "Microsoft Access Database Engine 2010 Redistributable" and be sure the' 'Red'
            Log '  BITNESS matches: run the 32-bit .bat with the 32-bit engine (AccessDatabaseEngine.exe,' 'Red'
            Log '  the one WITHOUT _X64), or the 64-bit .bat with the 64-bit engine.' 'Red'
            Log "  file: $p" 'Red'
            throw 'ACE engine unavailable'
        }
    }
}
Log 'Mapping which database holds which table...' 'Cyan'
foreach ($x in $sets) {
    $s = $x.Store
    foreach ($tbl in $NEED[$s]) {
        $found = @()
        foreach ($k in $KINDS) {           # primary first, so primary wins
            $key = "$s|$k"
            if (-not $script:conns.ContainsKey($key)) { continue }
            if (Has-Table $script:conns[$key] $tbl) { $found += $k }
        }
        if ($found.Count -eq 0) {
            Log "  $s : table [$tbl] is in NEITHER primary nor secondary" 'Red'
            continue
        }
        $use = $found[0]
        $script:tableHome["$s|$tbl"] = "$s|$use"
        if (-not $script:probeTable.ContainsKey("$s|$use")) { $script:probeTable["$s|$use"] = $tbl }
        if ($found.Count -gt 1) { Log "  $s : [$tbl] in both - using $use" 'Yellow' }
        else { Log "  $s : [$tbl] -> $use" }
    }
}
# every table we need must have a home, or stop before writing anything
$missing = @()
foreach ($x in $sets) {
    foreach ($tbl in $NEED[$x.Store]) {
        if (-not $script:tableHome.ContainsKey("$($x.Store)|$tbl")) { $missing += "$($x.Store).$tbl" }
    }
}
if ($missing.Count -gt 0) {
    Log "STOP: these tables were not found in primary or secondary: $($missing -join ', ')" 'Red'
    Log 'Check that the right database files were copied down. Nothing was changed.' 'Red'
    throw 'required tables missing'
}
# databases we never write to do not need backing up
$inUse = @{}
foreach ($v in $script:tableHome.Values) { $inUse[$v] = $true }
foreach ($key in @($script:conns.Keys)) {
    if (-not $inUse.ContainsKey($key)) {
        Log "  $key holds none of the tables we write - leaving it untouched" 'Yellow'
        try { $script:conns[$key].Close() } catch {}
        $script:conns.Remove($key)
    }
}

# ---- backups ----------------------------------------------------------------
if ($NoBackup) {
    Log 'Backups skipped (-NoBackup). The originals on the server are your only copy.' 'Yellow'
} else {
    foreach ($key in @($script:conns.Keys)) {
        $p = $script:dbPaths[$key]
        $stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
        $backup = [System.IO.Path]::ChangeExtension($p, $null) + "backup_$stamp.accdb"
        Copy-Item $p $backup
        Log "Backup $key -> $backup" 'Green'
    }
}

$MARK = '=== Great Plains history (imported ' + (Get-Date -Format 'MM/dd/yyyy') + ') ==='

# ================= preload lookup caches =====================================
Log 'Preloading lookup caches...' 'Cyan'
$custIds = @{}   # store -> HashSet of every customer ID in that store
$gpByName = @{}  # store -> map "last|first|address" -> ID (Source='GP' customers)
foreach ($s in $STORES) {
    $set = New-Object 'System.Collections.Generic.HashSet[int]'
    $tb = TableR $s 'Customers' 'SELECT ID FROM Customers' @()
    foreach ($row in $tb.Rows) { [void]$set.Add([int]$row.ID) }
    $custIds[$s] = $set
    $map = @{}
    $tb = TableR $s 'Customers' "SELECT ID, [Customer Last Name] AS ln, [Customer First Name] AS fn, [Address] AS ad FROM Customers WHERE Source = 'GP'" @()
    foreach ($row in $tb.Rows) { $map[((NK $row.ln) + '|' + (NK $row.fn) + '|' + (NK $row.ad))] = [int]$row.ID }
    $gpByName[$s] = $map
    Log "  $s : $($set.Count) customers ($($map.Count) from GP so far)"
}

# IDs handed out on earlier (possibly single-store) runs, so every store agrees
$idFile = Join-Path $here 'gp_assigned_ids.csv'
$assigned = @{}
if (Test-Path $idFile) {
    foreach ($r in (Import-Csv $idFile)) { $assigned[$r.nkey] = [int]$r.id }
    Log "  loaded $($assigned.Count) customer IDs assigned on earlier runs"
}
foreach ($s in $STORES) {
    foreach ($k in $gpByName[$s].Keys) { if (-not $assigned.ContainsKey($k)) { $assigned[$k] = $gpByName[$s][$k] } }
}
function Save-Ids($map, $path) {
    $out = @()
    foreach ($k in $map.Keys) { $out += (New-Object PSObject -Property @{ nkey = $k; id = $map[$k] }) }
    $out | Select-Object nkey, id | Export-Csv $path -NoTypeInformation
}

# note rows at Orem (only when Orem is part of this run)
$noteText = @{}
$doNotes = ($STORES -contains '1ORM')
if ($doNotes) {
    $tb = TableR '1ORM' 'Customer_PastHistory' 'SELECT CustID, PastHistory FROM Customer_PastHistory' @()
    foreach ($row in $tb.Rows) { $noteText[[int]$row.CustID] = "$($row.PastHistory)" }
    Log "  1ORM: $($noteText.Count) customer note rows"
} else {
    Log '  (Orem not in this run - purchase-history notes will be done when 1ORM is run)' 'Yellow'
}

# ================= STAGE 1: notes (Orem) + customers =========================
Log 'STAGE 1: notes + customers' 'Cyan'
if ($doNotes) {
    $rows = Import-Csv (Join-Path $here 'import_append_history.csv')
    $done=0; $skip=0; $errs=0
    foreach ($r in $rows) {
        try {
            $custId = [int]$r.clarity_id
            $existing = if ($noteText.ContainsKey($custId)) { $noteText[$custId] } else { $null }
            if ($null -ne $existing -and $existing -like '*Great Plains history*') { $skip++; continue }
            $block = $MARK + "`r`n" + $r.history
            if ($null -ne $existing) {
                $newText = if (IsBlank $existing) { $block } else { "$existing`r`n`r`n$block" }
                [void](ExecR '1ORM' 'Customer_PastHistory' 'UPDATE Customer_PastHistory SET PastHistory = ? WHERE CustID = ?' @($newText, $custId))
                $noteText[$custId] = $newText
            } else {
                [void](ExecR '1ORM' 'Customer_PastHistory' 'INSERT INTO Customer_PastHistory (CustID, PastHistory) VALUES (?, ?)' @($custId, $block))
                $noteText[$custId] = $block
            }
            $done++
        } catch { $errs++; Log "  note for customer $($r.clarity_id) error: $($_.Exception.Message)" 'Yellow' }
    }
    Log "Notes at Orem: $done added, $skip already present, $errs errors." 'Green'
}

$rows = Import-Csv (Join-Path $here 'import_new_customers.csv')
# id base: above everything known - the stores present AND every ID ever assigned
$maxId = 0
foreach ($s in $STORES) {
    $m = ScalarR $s 'Customers' 'SELECT MAX(ID) FROM Customers' @()
    if ($m -isnot [System.DBNull] -and $null -ne $m -and [int]$m -gt $maxId) { $maxId = [int]$m }
}
foreach ($v in $assigned.Values) { if ([int]$v -gt $maxId) { $maxId = [int]$v } }
$nextId = $maxId + 1000
Log "Inserting $($rows.Count) GP customers into $($STORES -join ', ') (new IDs from $nextId)..."
$INSSQL = ('INSERT INTO Customers (ID, [Customer Last Name], [Customer First Name], [Address], [City], [State], [Zip], ' +
    '[Phone Number (H)], [Source], [time], [Mail], [VIP], [TabAllowed], [HA_finance_charges], [HA_compound], ' +
    '[NeverSendStatement], [ShowNotesAtPOS], [Rewards]) VALUES (?,?,?,?,?,?,?,?,?,?,0,0,0,0,0,0,0,0)')
$ins=0; $heal=0; $skip=0; $errs=0; $i=0
foreach ($r in $rows) {
    $i++
    try {
        $nkey = (NK $r.lastname) + '|' + (NK $r.firstname) + '|' + (NK $r.address)
        # already given an ID - on this run, an earlier run, or in some store?
        $id = $null
        if ($assigned.ContainsKey($nkey)) { $id = $assigned[$nkey] }
        if ($null -eq $id) {
            foreach ($s in $STORES) {
                if ($gpByName[$s].ContainsKey($nkey)) { $id = $gpByName[$s][$nkey]; break }
            }
        }
        $isNew = ($null -eq $id)
        if ($isNew) { $id = $nextId; $nextId++ }
        $assigned[$nkey] = $id

        # make sure this customer exists in every store in this run, same ID
        $had = 0
        foreach ($s in $STORES) {
            if ($custIds[$s].Contains($id)) { $had++; continue }
            try {
                [void](ExecR $s 'Customers' $INSSQL @($id, $r.lastname, $r.firstname, $r.address, $r.city, $r.state, $r.zip, $r.phone, 'GP', (Get-Date)))
                [void]$custIds[$s].Add($id)
                $gpByName[$s][$nkey] = $id
            } catch {
                if ($_.Exception.Message -like '*duplicate*') { $had++; [void]$custIds[$s].Add($id) } else { throw }
            }
        }
        # purchase-history note at Orem (heals if a drop killed it last time)
        if ($doNotes -and -not (IsBlank $r.history) -and -not $noteText.ContainsKey($id)) {
            $block = $MARK + "`r`n" + $r.history
            [void](ExecR '1ORM' 'Customer_PastHistory' 'INSERT INTO Customer_PastHistory (CustID, PastHistory) VALUES (?, ?)' @($id, $block))
            $noteText[$id] = $block
        }
        if ($had -ge $STORES.Count) { $skip++ } elseif ($isNew) { $ins++ } else { $heal++ }
    } catch { $errs++; Log "  customer row $i ($($r.firstname) $($r.lastname)) error: $($_.Exception.Message)" 'Yellow' }
    if ($i % 2000 -eq 0) { Log "  customers: $i / $($rows.Count)..."; Save-Ids $assigned $idFile }
}
Save-Ids $assigned $idFile
Log "Customers: $ins inserted, $heal completed from a previous partial run, $skip already done, $errs errors." 'Green'
Log "  customer IDs remembered in $idFile - keep this file for the other stores." 'Green'

# ================= slips, per store ==========================================
foreach ($st in $STORES) {
    Log "STAGE slips -> $st" 'Cyan'
    $BASE = 900000
    $maxSlip = ScalarR $st 'POS_SalesSlip' "SELECT MAX(ID) FROM POS_SalesSlip WHERE ID >= $BASE" @()
    $slipId = if ($maxSlip -is [System.DBNull] -or $null -eq $maxSlip) { $BASE + 1 } else { [int]$maxSlip + 1 }
    $maxTx = ScalarR $st 'POS_Transactions' "SELECT MAX(TransactionID) FROM POS_Transactions WHERE TransactionID >= $BASE" @()
    $txId = if ($maxTx -is [System.DBNull] -or $null -eq $maxTx) { $BASE + 1 } else { [int]$maxTx + 1 }

    # preload what is already imported here (one read instead of a scan per slip)
    $markerMap = @{}   # 'GPIMPORT:xxx' -> slip ID
    $tb = TableR $st 'POS_SalesSlip' "SELECT ID, Notes FROM POS_SalesSlip WHERE ID >= $BASE AND Notes LIKE 'GPIMPORT:%'" @()
    foreach ($row in $tb.Rows) { $markerMap["$($row.Notes)"] = [int]$row.ID }
    $lineCount = @{}   # slip ID -> number of line items
    $tb = TableR $st 'POS_Transactions' "SELECT SalesSlipID AS S, COUNT(*) AS C FROM POS_Transactions WHERE SalesSlipID >= $BASE GROUP BY SalesSlipID" @()
    foreach ($row in $tb.Rows) { $lineCount[[int]$row.S] = [int]$row.C }
    Log "  already imported here: $($markerMap.Count) slips"

    $rows = Import-Csv (Join-Path $here "slips_$st.csv")
    $groups = $rows | Group-Object gpinv
    Log "  $($groups.Count) slips to import into $st"
    $ins=0; $skip=0; $nocust=0; $errs=0; $i=0
    foreach ($g in $groups) {
        $i++
        try {
            $r0 = $g.Group[0]
            $key = 'GPIMPORT:' + $r0.gpinv
            if ($markerMap.ContainsKey($key)) {
                $oldSid = $markerMap[$key]
                $lc = if ($lineCount.ContainsKey($oldSid)) { $lineCount[$oldSid] } else { 0 }
                if ($lc -ge $g.Group.Count) { $skip++; continue }
                # slip is there but lost its lines mid-write last time - rebuild it
                [void](ExecR $st 'POS_Transactions' 'DELETE FROM POS_Transactions WHERE SalesSlipID = ?' @($oldSid))
                [void](ExecR $st 'POS_SalesSlip' 'DELETE FROM POS_SalesSlip WHERE ID = ?' @($oldSid))
                $markerMap.Remove($key); $lineCount.Remove($oldSid)
            }
            $custId = $null
            if ($r0.clarity_id -ne '') {
                $cid = [int][double]$r0.clarity_id
                if ($custIds[$st].Contains($cid)) { $custId = $cid }
            }
            if ($null -eq $custId) {
                $nkey = (NK $r0.lastname) + '|' + (NK $r0.firstname) + '|' + (NK $r0.address)
                if ($gpByName[$st].ContainsKey($nkey)) { $custId = $gpByName[$st][$nkey] }
            }
            if ($null -eq $custId) { $nocust++; continue }

            $total = ($g.Group | Measure-Object -Property amount -Sum).Sum
            $when = ([datetime]$r0.docdate).AddHours(12)
            $sid = $slipId; $slipId++
            [void](ExecR $st 'POS_SalesSlip' ('INSERT INTO POS_SalesSlip (ID, SalesSlipNumber, [TimeStamp], SystemEnteredDate, CustID, TotalSale, TotalDeposit, TotalTax, TotalBalance, ' +
                'SoldBy, SlipType, SlipTypeMsg, CustInfo, Notes, PreventMail, HasALayawayPayment, HasAJobPayment, LayawayPaymentOnly, JobPaymentOnly, ' +
                'ReturnedItems, Cancelled, HouseAccountSale, Commissionable) VALUES (?,?,?,?,?,?,?,0,0,?,1,?,?,?,0,0,0,0,0,0,0,0,0)') `
                @($sid, $sid, $when, (Get-Date), $custId, [double]$total, [double]$total,
                  $r0.clerk, 'Sale', ($r0.firstname + ' ' + $r0.lastname).Trim(), $key))
            $markerMap[$key] = $sid
            foreach ($ln in $g.Group) {
                $tid = $txId; $txId++
                [void](ExecR $st 'POS_Transactions' ('INSERT INTO POS_Transactions (TransactionID, SalesSlipID, TransactionType, TransactionPrice, ItemNumber, Description, quantity, ' +
                    'RegularPrice, FinalPrice, SoldForPrice, ItemTax, FinalItemTax, Selected, Taxable, TakeFromReturn, LayawayPickup, LayawayPaidInFull, ' +
                    'Donation, DepositNoBalance, JobDepositRefund, JobPickedUp, FirstDeposit, PayInFull, Pickup) ' +
                    'VALUES (?,?,17,?,?,?,1,?,?,?,0,0,0,0,0,0,0,0,0,0,0,1,1,1)') `
                    @($tid, $sid, [double]$ln.amount, 'GP', $ln.descr, [double]$ln.amount, [double]$ln.amount, [double]$ln.amount))
            }
            $lineCount[$sid] = $g.Group.Count
            $ins++
        } catch { $errs++; Log "  slip $($g.Name) error: $($_.Exception.Message)" 'Yellow' }
        if ($i % 2000 -eq 0) { Log "  $st slips: $i / $($groups.Count)..." }
    }
    Log "$st done: $ins imported, $skip already done, $nocust customer-not-found, $errs errors." 'Green'
}
} catch {
    $script:failed = $_.Exception.Message
} finally {
    foreach ($k in @($script:conns.Keys)) { try { $script:conns[$k].Close() } catch {} }
    try { $script:runLock.Close(); Remove-Item $sentinel -Force } catch { }
}

if ($script:failed) {
    Log "RUN STOPPED: $script:failed" 'Red'
    Log 'See the lines above for what to fix, then run again.' 'Red'
    if (-not $Auto) { Read-Host 'Enter to exit' }
    exit 1
}
if ($script:lockWaits -gt 0) { Log "Lock conflicts waited out during the run: $script:lockWaits" 'Yellow' }
if ($script:reopens -gt 0)  { Log "Connections reopened during the run: $script:reopens" 'Yellow' }
Log '=== RUN COMPLETE ===' 'Cyan'
Log 'Spot-check a few customers, then copy the database files back to the server.' 'Cyan'
if (-not $Auto) { Read-Host 'Press Enter to close' }
