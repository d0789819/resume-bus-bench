# Reset the ESP32 through the CH340 auto-reset circuit (RTS -> EN) and capture
# the boot log at 115200 baud. DTR stays released, so GPIO0 stays high and the
# board boots the normal firmware (no download mode, nothing is written).
param([int]$Seconds = 50, [string]$Port = "")
$ErrorActionPreference = "Stop"

$ports = Get-CimInstance Win32_PnPEntity | Where-Object { $_.Name -match '\(COM\d+\)' }
foreach ($p in $ports) { "PNP: " + $p.Name + " | " + $p.DeviceID }
if (-not $Port) {
  $ch = $ports | Where-Object { $_.Name -match 'CH34' -or $_.DeviceID -match 'VID_1A86' } | Select-Object -First 1
  if (-not $ch) { "ERROR: no CH340 (VID_1A86) serial port found"; exit 2 }
  $Port = [regex]::Match($ch.Name, 'COM\d+').Value
}
"PORT: $Port"

$sp = New-Object System.IO.Ports.SerialPort $Port, 115200, ([System.IO.Ports.Parity]::None), 8, ([System.IO.Ports.StopBits]::One)
$sp.DtrEnable = $false
$sp.RtsEnable = $false
$sp.Encoding = [System.Text.Encoding]::UTF8
try { $sp.Open() } catch { "ERROR: cannot open ${Port}: $($_.Exception.Message)"; exit 3 }

Start-Sleep -Milliseconds 100
$sp.RtsEnable = $true      # EN low
Start-Sleep -Milliseconds 150
$sp.RtsEnable = $false     # EN high -> boot
"RESET: $(Get-Date -Format o)"
"----- serial output -----"

$all = New-Object System.Text.StringBuilder
$start = Get-Date
$stopAt = $start.AddSeconds($Seconds)
$extendedUntil = $null
while ((Get-Date) -lt $stopAt) {
  $chunk = $sp.ReadExisting()
  if ($chunk) { [void]$all.Append($chunk) }
  $text = $all.ToString()
  if (-not $extendedUntil -and ($text -match 'WiFi up: \d+\.\d+\.\d+\.\d+' -or $text -match 'No WiFi\. Join')) {
    $extendedUntil = (Get-Date).AddSeconds(3)
    $stopAt = $extendedUntil
  }
  Start-Sleep -Milliseconds 100
}
$sp.Close()
$all.ToString()
"----- end ($([int]((Get-Date) - $start).TotalSeconds) s) -----"
