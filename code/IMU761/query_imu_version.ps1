param(
    [string]$PortName = ""
)

$ErrorActionPreference = "Stop"

$availablePorts = [System.IO.Ports.SerialPort]::GetPortNames() | Sort-Object
Write-Host "Available ports: $($availablePorts -join ', ')"

if ([string]::IsNullOrWhiteSpace($PortName)) {
    $PortName = Read-Host "Enter IMU COM port (example: COM1)"
}

if ($PortName -notmatch '^COM\d+$') {
    Write-Error "Invalid port name: $PortName"
    exit 2
}

$serial = [System.IO.Ports.SerialPort]::new(
    $PortName,
    115200,
    [System.IO.Ports.Parity]::None,
    8,
    [System.IO.Ports.StopBits]::One
)
$serial.Handshake = [System.IO.Ports.Handshake]::None
$serial.Encoding = [System.Text.Encoding]::ASCII
$serial.ReadTimeout = 500
$serial.WriteTimeout = 1000

try {
    $serial.Open()
    $serial.DiscardInBuffer()

    $command = "LOGVERSION" + [char]13 + [char]10
    $serial.Write($command)

    $stream = [System.IO.MemoryStream]::new()
    $deadline = [DateTime]::UtcNow.AddSeconds(3)

    while ([DateTime]::UtcNow -lt $deadline) {
        $count = $serial.BytesToRead
        if ($count -gt 0) {
            $buffer = [byte[]]::new($count)
            $read = $serial.Read($buffer, 0, $count)
            $stream.Write($buffer, 0, $read)
        }
        Start-Sleep -Milliseconds 20
    }

    $received = $stream.ToArray()
    if ($received.Length -eq 0) {
        Write-Warning "No response received. Check power, TX/RX crossing, GND, port, and baud rate."
        exit 3
    }

    $text = [System.Text.Encoding]::ASCII.GetString($received)
    $printable = $text -replace '[^\x09\x0A\x0D\x20-\x7E]', '.'

    Write-Host "`nReceived $($received.Length) bytes:"
    Write-Host $printable

    $versionLines = $printable -split "`r?`n" | Where-Object {
        $_ -match '(?i)VERSION|V\d+[_.]\d+'
    }

    if ($versionLines) {
        Write-Host "`nPossible version response:" -ForegroundColor Green
        $versionLines | ForEach-Object { Write-Host $_ -ForegroundColor Green }
    }
    else {
        Write-Warning "Data was received, but no clear VERSION text was detected."
    }
}
catch {
    Write-Error "Serial query failed: $($_.Exception.Message). Close Bootloader and other serial programs, then retry."
    exit 1
}
finally {
    if ($serial.IsOpen) {
        $serial.Close()
    }
    $serial.Dispose()
}
