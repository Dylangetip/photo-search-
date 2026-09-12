# Find_Ring_By_Photo.ps1 - find an item in inventory from a photograph.
#
# Compares your photo against every picture in the Clarity image folder using a
# perceptual hash, and writes an HTML page of the closest matches with the item
# number, description, store and price for each.
#
#     Find_Ring_By_Photo.ps1 -Photo "C:\Users\bwilde\Desktop\ring.jpg"
#
# -ImageFolder <path>  the Clarity picture folder, found automatically if omitted
# -Top <n>             how many matches to show, default 40
# -Rebuild             recompute the hash cache from scratch
# -OnHandOnly          only show items currently in a store
#
# The first run reads all ~49,000 pictures and takes a while, maybe 15 to 30
# minutes. It saves image_hashes.csv, so every run after that is quick.
#
# READ ONLY. It never writes to a database and never changes a picture.

param(
  [Parameter(Mandatory=$true)][string]$Photo,
  [string]$ImageFolder,
  [int]$Top = 40,
  [switch]$Rebuild,
  [switch]$OnHandOnly,
  [switch]$Auto
)

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Add-Type -AssemblyName System.Drawing
function Say($m,$c='Gray'){ Write-Host $m -ForegroundColor $c }
function Die($m){ Say $m 'Red'; if(-not $Auto){Read-Host 'Enter to exit'}; exit 1 }

if (-not (Test-Path $Photo)) { Die "Cannot find your photo: $Photo" }
$lookupPath = Join-Path $here 'item_lookup.csv'
if (-not (Test-Path $lookupPath)) { Die "item_lookup.csv is not in this folder." }

# ---------- hashing ---------------------------------------------------------
$HEX = '0123456789abcdef'
function Pack-Bits($bits) {
    $sb = New-Object System.Text.StringBuilder
    for ($i=0; $i -lt 64; $i+=4) {
        $v = 0
        for ($j=0; $j -lt 4; $j++) { if ($bits[$i+$j]) { $v = $v -bor (1 -shl (3-$j)) } }
        [void]$sb.Append($HEX[$v])
    }
    return $sb.ToString()
}
# dHash: shrink to 9x8 grey, each bit is "is this pixel brighter than the one to its left"
function Hash-Region($src, $x, $y, $w, $h) {
    $s = New-Object System.Drawing.Bitmap(9,8)
    $g = [System.Drawing.Graphics]::FromImage($s)
    $g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
    $dst = New-Object System.Drawing.Rectangle(0,0,9,8)
    $srcR = New-Object System.Drawing.Rectangle($x,$y,$w,$h)
    $g.DrawImage($src,$dst,$srcR,[System.Drawing.GraphicsUnit]::Pixel)
    $g.Dispose()
    $bits = New-Object bool[] 64
    $k = 0
    for ($yy=0; $yy -lt 8; $yy++) {
        $prev = 0.0
        for ($xx=0; $xx -lt 9; $xx++) {
            $p = $s.GetPixel($xx,$yy)
            $lum = 0.299*$p.R + 0.587*$p.G + 0.114*$p.B
            if ($xx -gt 0) { $bits[$k] = ($lum -gt $prev); $k++ }
            $prev = $lum
        }
    }
    $s.Dispose()
    return (Pack-Bits $bits)
}
# two hashes per picture: the whole thing, and the middle 70 percent. Comparing
# centre against centre survives a different border, overlay or crop.
function Hash-Image($path) {
    $img = $null
    try {
        $img = [System.Drawing.Image]::FromFile($path)
        $w = $img.Width; $h = $img.Height
        if ($w -lt 4 -or $h -lt 4) { return $null }
        $full = Hash-Region $img 0 0 $w $h
        $cw = [int]($w*0.70); $ch = [int]($h*0.70)
        $cx = [int](($w-$cw)/2); $cy = [int](($h-$ch)/2)
        $cen = Hash-Region $img $cx $cy $cw $ch
        return @{ full=$full; center=$cen }
    } catch { return $null }
    finally { if ($img) { $img.Dispose() } }
}
$POP = @(0,1,1,2,1,2,2,3,1,2,2,3,2,3,3,4)
function HexDist($a,$b) {
    if (-not $a -or -not $b -or $a.Length -ne 16 -or $b.Length -ne 16) { return 99 }
    $d = 0
    for ($i=0; $i -lt 16; $i++) {
        $d += $POP[ ([Convert]::ToInt32($a[$i],16)) -bxor ([Convert]::ToInt32($b[$i],16)) ]
    }
    return $d
}

# ---------- find the picture folder ----------------------------------------
$lookup = @(Import-Csv $lookupPath)
Say "$($lookup.Count) catalogue pictures known." 'Cyan'
function Find-ImageFolder($rows) {
    $probe = @($rows | Where-Object { $_.image_file } | Select-Object -First 12 | ForEach-Object { $_.image_file })
    $roots = @()
    foreach ($d in (Get-PSDrive -PSProvider FileSystem)) {
        $r = $d.Root
        $roots += @($r,(Join-Path $r 'images'),(Join-Path $r 'Images'),(Join-Path $r 'IMAGES'),
                    (Join-Path $r 'pictures'),(Join-Path $r 'Pictures'),
                    (Join-Path $r 'clarity\images'),(Join-Path $r 'Clarity\Images'))
        foreach ($s in '1ORM','2SDY','3MRY') { $roots += (Join-Path $r "$s\images") }
    }
    $roots += @((Join-Path $here 'images'), $here)
    foreach ($c in ($roots | Select-Object -Unique)) {
        try { if (Test-Path $c) { foreach ($f in $probe) { if (Test-Path (Join-Path $c $f)) { return $c } } } } catch {}
    }
    return $null
}
if (-not $ImageFolder) { Say 'Looking for the Clarity picture folder...'; $ImageFolder = Find-ImageFolder $lookup }
if (-not $ImageFolder -or -not (Test-Path $ImageFolder)) {
    Die "Could not find the picture folder. Re-run with -ImageFolder ""N:\path\to\images"""
}
Say "Picture folder: $ImageFolder" 'Green'

# ---------- hash cache ------------------------------------------------------
$cachePath = Join-Path $here 'image_hashes.csv'
$cache = @{}
if ((Test-Path $cachePath) -and -not $Rebuild) {
    foreach ($r in (Import-Csv $cachePath)) { $cache[$r.file] = @{ full=$r.full; center=$r.center } }
    Say "Loaded $($cache.Count) hashes from image_hashes.csv." 'Green'
}
$files = @(Get-ChildItem -Path $ImageFolder -File -ErrorAction SilentlyContinue |
           Where-Object { $_.Extension -match '(?i)\.(jpg|jpeg|png|bmp|gif)$' })
Say "$($files.Count) picture files in the folder."
$todo = @($files | Where-Object { -not $cache.ContainsKey($_.Name) })
if ($todo.Count -gt 0) {
    Say "Hashing $($todo.Count) pictures. The first run is the slow one; after this it is cached." 'Yellow'
    $n=0; $bad=0; $t0=Get-Date
    foreach ($f in $todo) {
        $n++
        $h = Hash-Image $f.FullName
        if ($h) { $cache[$f.Name] = $h } else { $bad++ }
        if ($n % 2000 -eq 0) {
            $el=((Get-Date)-$t0).TotalSeconds
            $rate=[Math]::Max($n/$el,0.001)
            Say ("  {0} / {1}   {2:N0} per second, about {3:N0} min left" -f $n,$todo.Count,$rate,(($todo.Count-$n)/$rate/60))
        }
    }
    Say "  hashed $($n-$bad), skipped $bad that would not open." 'Green'
    $rows = foreach ($k in $cache.Keys) { [PSCustomObject]@{ file=$k; full=$cache[$k].full; center=$cache[$k].center } }
    $rows | Export-Csv $cachePath -NoTypeInformation
    Say "  cache saved to $cachePath" 'Green'
}

# ---------- hash the photo we are looking for -------------------------------
Say "Hashing your photo..." 'Cyan'
$q = Hash-Image $Photo
if (-not $q) { Die "Could not read $Photo as an image." }

# ---------- rank ------------------------------------------------------------
$byFile = @{}
foreach ($r in $lookup) {
    $k = $r.image_file.ToLower()
    if (-not $byFile.ContainsKey($k)) { $byFile[$k] = $r }
}
$res = New-Object System.Collections.ArrayList
foreach ($k in $cache.Keys) {
    $h = $cache[$k]
    $d1 = HexDist $q.full   $h.full
    $d2 = HexDist $q.center $h.center
    $d  = [Math]::Min($d1,$d2)
    $meta = $byFile[$k.ToLower()]
    if ($OnHandOnly -and (-not $meta -or -not $meta.onhand)) { continue }
    [void]$res.Add([PSCustomObject]@{
        file=$k; dist=$d; dfull=$d1; dcenter=$d2
        item = if($meta){$meta.inventory_number}else{'(not in the catalogue list)'}
        desc = if($meta){$meta.desc}else{''}
        vendor = if($meta){$meta.vendor}else{''}
        onhand = if($meta){$meta.onhand}else{''}
        cost = if($meta){$meta.cost}else{''}
        retail = if($meta){$meta.retail}else{''}
    })
}
$best = @($res | Sort-Object dist | Select-Object -First $Top)
Say ''
Say "Closest matches (0 means identical, under about 12 is a strong match):" 'Cyan'
$i=0
foreach ($b in ($best | Select-Object -First 10)) {
    $i++
    Say ("  {0,2}. distance {1,2}  {2,-14} {3,-20} {4}" -f $i,$b.dist,$b.item,$b.onhand,$b.desc)
}

# ---------- results page ----------------------------------------------------
function Emb($path,$w) {
    try {
        $img=[System.Drawing.Image]::FromFile($path)
        $r=$w/[double]$img.Width; $nh=[int][Math]::Max(1,$img.Height*$r)
        $bmp=New-Object System.Drawing.Bitmap($w,$nh)
        $g=[System.Drawing.Graphics]::FromImage($bmp)
        $g.InterpolationMode=[System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
        $g.DrawImage($img,0,0,$w,$nh); $g.Dispose(); $img.Dispose()
        $ms=New-Object System.IO.MemoryStream
        $bmp.Save($ms,[System.Drawing.Imaging.ImageFormat]::Jpeg); $bmp.Dispose()
        $b=[Convert]::ToBase64String($ms.ToArray()); $ms.Dispose(); return $b
    } catch { return $null }
}
function E($s){ if($null -eq $s){return ''} ("$s").Replace('&','&amp;').Replace('<','&lt;').Replace('>','&gt;') }
$css=@'
<style>
body{font-family:Segoe UI,Arial,sans-serif;margin:18px;color:#111}
h1{font-size:19px;color:#1F3864;margin:0 0 2px}.sub{font-size:11px;color:#555;margin-bottom:14px}
.q{border:2px solid #1F3864;border-radius:6px;padding:8px;width:300px;margin-bottom:18px}
.q img{width:100%;border-radius:3px}
.grid{display:flex;flex-wrap:wrap;gap:8px}
.c{width:196px;border:1px solid #ccc;border-radius:5px;padding:6px;page-break-inside:avoid}
.c img{width:100%;border-radius:3px;background:#f4f4f4}
.d{font-weight:700;font-size:11px;border-radius:3px;padding:1px 5px;display:inline-block;margin-top:4px}
.hot{background:#D8F0D8;color:#155724}.warm{background:#FFF3CD;color:#7A5B00}.cold{background:#EEE;color:#666}
.sku{font-weight:700;font-size:11.5px;color:#1F3864;margin-top:3px}
.l{font-size:9.5px;color:#444;line-height:1.35}
.oh{background:#EAF1E4;font-weight:700;padding:0 3px;border-radius:2px}
</style>
'@
$sb=New-Object System.Text.StringBuilder
[void]$sb.Append("<html><head><meta charset='utf-8'><title>Photo match</title>$css</head><body>")
[void]$sb.Append("<h1>Finding this piece in inventory</h1>")
[void]$sb.Append("<div class='sub'>Compared against $($cache.Count) catalogue pictures. Distance 0 is identical; under about 12 is a strong match; over 20 is probably a different piece. Green means it is on hand somewhere.</div>")
$qb=Emb $Photo 300
if($qb){[void]$sb.Append("<div class='q'><img src='data:image/jpeg;base64,$qb'/><div class='l'>your photo</div></div>")}
[void]$sb.Append("<div class='grid'>")
foreach($b in $best){
    $p=Join-Path $ImageFolder $b.file
    $e=Emb $p 190
    $cls = if($b.dist -le 12){'d hot'} elseif($b.dist -le 20){'d warm'} else {'d cold'}
    [void]$sb.Append("<div class='c'>")
    if($e){[void]$sb.Append("<img src='data:image/jpeg;base64,$e'/>")}
    [void]$sb.Append("<div class='$cls'>distance $($b.dist)</div>")
    [void]$sb.Append("<div class='sku'>$(E $b.item)</div>")
    if($b.onhand){[void]$sb.Append("<div class='l'><span class='oh'>$(E $b.onhand)</span></div>")}
    [void]$sb.Append("<div class='l'>$(E $b.desc)</div>")
    if($b.vendor){[void]$sb.Append("<div class='l'>$(E $b.vendor)</div>")}
    if($b.retail){[void]$sb.Append("<div class='l'>cost `$$(E $b.cost) &middot; ret `$$(E $b.retail)</div>")}
    [void]$sb.Append("</div>")
}
[void]$sb.Append('</div></body></html>')
$out=Join-Path $here 'Photo_Match_Results.html'
[IO.File]::WriteAllText($out,$sb.ToString(),[Text.Encoding]::UTF8)
Say ''
Say "Results written to $out" 'Green'
if(-not $Auto){ Read-Host 'Press Enter to close' }
