# Make_Photo_Sheets.ps1 - build printable photo sheets of the aged gold stock.
#
# Reads gold_items.csv (next to this script), finds each item's photo in the
# Clarity image folder, and writes one self contained HTML page per store. The
# photos are embedded in the page, so you can email the file or open it on any
# machine and it still shows the pictures. Open it and print to PDF if you want
# paper to walk the cases with.
#
# It finds the image folder itself by looking for one of the known photo files.
# If it cannot, point it at the folder:
#     Make_Photo_Sheets.ps1 -ImageFolder "N:\images"
#
# READ ONLY. It never writes to a database and never changes a photo.

param([string]$ImageFolder, [int]$ThumbWidth = 240, [switch]$Auto)

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Add-Type -AssemblyName System.Drawing

function Say($m, $c='Gray') { Write-Host $m -ForegroundColor $c }

$csv = Join-Path $here 'gold_items.csv'
if (-not (Test-Path $csv)) { Say "gold_items.csv is not in this folder." 'Red'; if(-not $Auto){Read-Host 'Enter'}; exit 1 }
$items = @(Import-Csv $csv)
Say "$($items.Count) items to sheet." 'Cyan'

# ---- find the image folder -------------------------------------------------
function Find-ImageFolder($items) {
    $probe = @($items | Where-Object { $_.image_file } | Select-Object -First 12 |
               ForEach-Object { $_.image_file })
    if ($probe.Count -eq 0) { return $null }
    $roots = @()
    foreach ($d in (Get-PSDrive -PSProvider FileSystem)) {
        $r = $d.Root
        $roots += @($r,
            (Join-Path $r 'images'), (Join-Path $r 'Images'), (Join-Path $r 'IMAGES'),
            (Join-Path $r 'pictures'), (Join-Path $r 'Pictures'),
            (Join-Path $r 'clarity\images'), (Join-Path $r 'Clarity\Images'),
            (Join-Path $r 'clarity\pictures'))
        foreach ($s in '1ORM','2SDY','3MRY') {
            $roots += @((Join-Path $r "$s\images"), (Join-Path $r "clarity\$s\images"))
        }
    }
    $roots += @((Join-Path $here 'images'), $here)
    foreach ($c in ($roots | Select-Object -Unique)) {
        try {
            if (-not (Test-Path $c)) { continue }
            foreach ($f in $probe) {
                if (Test-Path (Join-Path $c $f)) { return $c }
            }
        } catch { }
    }
    return $null
}

if (-not $ImageFolder) {
    Say 'Looking for the Clarity image folder...'
    $ImageFolder = Find-ImageFolder $items
}
if (-not $ImageFolder -or -not (Test-Path $ImageFolder)) {
    Say '' ; Say 'Could not find the folder holding the item photos.' 'Yellow'
    Say 'Find where Clarity keeps its item pictures (a folder full of files like' 'Yellow'
    Say "$($items[0].image_file)) and run this again as:" 'Yellow'
    Say '    Make_Photo_Sheets.ps1 -ImageFolder "N:\path\to\images"' 'Yellow'
    if (-not $Auto) { Read-Host 'Enter to exit' }
    exit 1
}
Say "Image folder: $ImageFolder" 'Green'

# build a lookup of the folder once, so a odd cased name still matches
$byName = @{}
foreach ($f in (Get-ChildItem -Path $ImageFolder -File -ErrorAction SilentlyContinue)) {
    $k = $f.Name.ToLower()
    if (-not $byName.ContainsKey($k)) { $byName[$k] = $f.FullName }
}
Say "  $($byName.Count) image files in that folder."

# ---- thumbnail to base64 ---------------------------------------------------
# resizing needs System.Drawing, which is not present on every machine. Probe it
# once, and fall back to embedding small files as they are rather than failing.
$script:CanResize = $false
try {
    $probe = New-Object System.Drawing.Bitmap(2,2)
    $probe.Dispose(); $script:CanResize = $true
} catch { }
if (-not $script:CanResize) {
    Say 'Image resizing is unavailable on this machine, so photos are embedded at' 'Yellow'
    Say 'their original size and anything over 400 KB is skipped. The sheets still' 'Yellow'
    Say 'build, they are just larger.' 'Yellow'
}

# Two separate functions on purpose. A function whose body mentions
# System.Drawing types throws a type initializer error the moment it is entered
# on a machine where GDI+ will not load, before any guard inside it can run, so
# the fallback path must not reference those types at all.
function Get-ThumbResized($path, $w) {
    try {
        $img = [System.Drawing.Image]::FromFile($path)
        $ratio = $w / [double]$img.Width
        $nh = [int]([Math]::Max(1, $img.Height * $ratio))
        $bmp = New-Object System.Drawing.Bitmap($w, $nh)
        $g = [System.Drawing.Graphics]::FromImage($bmp)
        $g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
        $g.DrawImage($img, 0, 0, $w, $nh)
        $g.Dispose(); $img.Dispose()
        $ms = New-Object System.IO.MemoryStream
        $bmp.Save($ms, [System.Drawing.Imaging.ImageFormat]::Jpeg)
        $bmp.Dispose()
        $b64 = [Convert]::ToBase64String($ms.ToArray())
        $ms.Dispose()
        return $b64
    } catch { return $null }
}
function Get-RawEmbed($path) {
    try {
        $fi = Get-Item $path
        if ($fi.Length -gt 400KB) { return $null }
        return [Convert]::ToBase64String([IO.File]::ReadAllBytes($path))
    } catch { return $null }
}
function Get-Photo($path, $w) {
    if ($script:CanResize) { return (Get-ThumbResized $path $w) }
    return (Get-RawEmbed $path)
}
function E($s) {
    if ($null -eq $s) { return '' }
    return ("$s").Replace('&','&amp;').Replace('<','&lt;').Replace('>','&gt;')
}

$CSS = @'
<style>
 body{font-family:Segoe UI,Arial,sans-serif;margin:24px;color:#111;background:#fff}
 h1{font-size:20px;color:#1F3864;margin:0 0 2px}
 .sub{font-size:11px;color:#555;margin-bottom:16px}
 .grid{display:flex;flex-wrap:wrap;gap:12px}
 .card{width:250px;border:1px solid #c8c8c8;border-radius:5px;padding:8px;page-break-inside:avoid;background:#fff}
 .card img{width:100%;border-radius:3px;display:block;background:#f4f4f4}
 .noimg{width:100%;height:150px;background:#f4f4f4;border-radius:3px;display:flex;
        align-items:center;justify-content:center;color:#999;font-size:11px}
 .sku{font-weight:700;font-size:12px;margin-top:6px;color:#1F3864}
 .desc{font-size:10.5px;line-height:1.35;margin:3px 0;height:42px;overflow:hidden;color:#333}
 table.f{width:100%;font-size:10.5px;border-collapse:collapse;margin-top:4px}
 table.f td{padding:1px 0}
 table.f td.k{color:#666;width:46%}
 .age{background:#FBE9E9;font-weight:700}
 @media print{ body{margin:8mm} .card{width:31%} }
</style>
'@

$stores = $items | ForEach-Object { $_.store } | Select-Object -Unique | Sort-Object
$made = @()
foreach ($st in $stores) {
    $rows = @($items | Where-Object { $_.store -eq $st })
    $sb = New-Object System.Text.StringBuilder
    [void]$sb.Append("<html><head><meta charset='utf-8'><title>$st aged gold</title>$CSS</head><body>")
    [void]$sb.Append("<h1>$st - gold rings and bands, 5 years and older</h1>")
    $cost = ($rows | Measure-Object -Property cost -Sum).Sum
    [void]$sb.Append("<div class='sub'>$($rows.Count) pieces, `$$('{0:N0}' -f $cost) at cost. Oldest first. Solid gold only; memo goods and vendor samples excluded.</div><div class='grid'>")
    $n=0; $miss=0
    foreach ($r in $rows) {
        $n++
        $b64 = $null
        if ($r.image_file) {
            $k = $r.image_file.ToLower()
            if ($byName.ContainsKey($k)) {
                try { $b64 = Get-Photo $byName[$k] $ThumbWidth } catch { $b64 = $null }
            }
        }
        [void]$sb.Append("<div class='card'>")
        if ($b64) { [void]$sb.Append("<img src='data:image/jpeg;base64,$b64'/>") }
        else { $miss++; [void]$sb.Append("<div class='noimg'>no photo on file</div>") }
        [void]$sb.Append("<div class='sku'>$(E $r.inventory_number)</div>")
        [void]$sb.Append("<div class='desc'>$(E $r.description)</div>")
        [void]$sb.Append("<table class='f'>")
        [void]$sb.Append("<tr><td class='k'>Age</td><td class='age'>$(E $r.years_old) yrs</td></tr>")
        [void]$sb.Append("<tr><td class='k'>Received</td><td>$(E $r.received)</td></tr>")
        [void]$sb.Append("<tr><td class='k'>Karat</td><td>$(E $r.karat)</td></tr>")
        [void]$sb.Append("<tr><td class='k'>Category</td><td>$(E $r.category)</td></tr>")
        [void]$sb.Append("<tr><td class='k'>Cost</td><td>`$$('{0:N0}' -f [double]$r.cost)</td></tr>")
        [void]$sb.Append("<tr><td class='k'>Retail</td><td>`$$('{0:N0}' -f [double]$r.retail)</td></tr>")
        [void]$sb.Append("</table></div>")
        if ($n % 25 -eq 0) { Say "  $st : $n / $($rows.Count)..." }
    }
    [void]$sb.Append('</div></body></html>')
    $out = Join-Path $here "Photo_Sheet_$st.html"
    [IO.File]::WriteAllText($out, $sb.ToString(), [Text.Encoding]::UTF8)
    $mb = [Math]::Round((Get-Item $out).Length/1MB,1)
    Say "$st : $($rows.Count) pieces, $miss without a photo -> $out  ($mb MB)" 'Green'
    $made += $out
}
Say ''
Say 'Done. Open a sheet and use Print to PDF if you want paper.' 'Cyan'
foreach ($m in $made) { Say "   $m" }
if (-not $Auto) { Read-Host 'Press Enter to close' }
