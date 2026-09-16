param(
    [string]$PortName = "COM1"
)

$ErrorActionPreference = "Stop"
$serial = [System.IO.Ports.SerialPort]::new(
    $PortName,
    115200,
    [System.IO.Ports.Parity]::None,
    8,
    [System.IO.Ports.StopBits]::One
)
$serial.Handshake = [System.IO.Ports.Handshake]::None
$serial.Encoding = [System.Text.Encoding]::ASCII
$serial.ReadTimeout = 1000
$serial.WriteTimeout = 1000
$testText = "IMU761_UART_LOOPBACK_12345"

try {
    $serial.Open()
    $serial.DiscardInBuffer()
    $serial.DiscardOutBuffer()
    $serial.Write($testText)
    Start-Sleep -Milliseconds 500
    $received = $serial.ReadExisting()

    Write-Host "Sent:     $testText"
    Write-Host "Received: $received"

    if ($received -eq $testText) {
        Write-Host "PASS: USB-UART transmit and receive are working." -ForegroundColor Green
        exit 0
    }

    Write-Host "FAIL: Loopback data did not match. Check that adapter TXD and RXD are directly connected." -ForegroundColor Red
    exit 1
}
catch {
    Write-Error "Loopback test failed: $($_.Exception.Message)"
    exit 2
}
finally {
    if ($serial.IsOpen) {
        $serial.Close()
    }
    $serial.Dispose()
}
