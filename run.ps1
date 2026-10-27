# Generate Markdown and HTML documentation for the nearest Power BI project.
$ErrorActionPreference = "Stop"
$PackageRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $PackageRoot

function Find-Python {
    $candidates = @(
        @{ Exe = "py"; Prefix = @("-3") },
        @{ Exe = "python"; Prefix = @() }
    )
    foreach ($candidate in $candidates) {
        if (-not (Get-Command $candidate.Exe -ErrorAction SilentlyContinue)) {
            continue
        }
        $previous = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        $raw = & $candidate.Exe @($candidate.Prefix) -c "import sys; print(sys.version_info[0]); print(sys.version_info[1]); print(sys.executable)" 2>$null
        $ErrorActionPreference = $previous
        if ($LASTEXITCODE -ne 0 -or -not $raw) {
            continue
        }
        $lines = @($raw)
        if ($lines.Count -lt 3) {
            continue
        }
        $major = 0
        $minor = 0
        if (-not [int]::TryParse($lines[0], [ref]$major) -or -not [int]::TryParse($lines[1], [ref]$minor)) {
            continue
        }
        if ($major -gt 3 -or ($major -eq 3 -and $minor -ge 10)) {
            return @{ Exe = $candidate.Exe; Prefix = $candidate.Prefix; Executable = $lines[2] }
        }
    }
    Write-Host "Python 3.10 or newer was not found on PATH."
    Write-Host "Install it from https://www.python.org/downloads/ and check 'Add python.exe to PATH'."
    exit 1
}

$python = Find-Python
Write-Host "Using $($python.Executable)"

$previous = $ErrorActionPreference
$ErrorActionPreference = "Continue"
& $python.Exe @($python.Prefix) -c "import markdown_it" 2>$null
$markdownReady = $LASTEXITCODE -eq 0
$ErrorActionPreference = $previous
if (-not $markdownReady) {
    Write-Host "Installing markdown-it-py..."
    & $python.Exe @($python.Prefix) -m pip install -r (Join-Path $PackageRoot "requirements.txt")
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}

& $python.Exe @($python.Prefix) (Join-Path $PackageRoot "tools\generate_docs.py")
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
& $python.Exe @($python.Prefix) (Join-Path $PackageRoot "tools\export_html.py")
exit $LASTEXITCODE
