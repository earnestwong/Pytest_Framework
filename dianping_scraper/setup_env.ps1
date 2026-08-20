<#
.SYNOPSIS
  Dianping scraper - environment setup
.DESCRIPTION
  Auto install: Python 3.12, mitmproxy, pywin32, CA cert
.EXAMPLE
  .\setup_env.ps1
  .\setup_env.ps1 -BuildExe
#>
param(
    [switch]$BuildExe,
    [string]$PythonVersion = "3.12.4"
)

$ErrorActionPreference = "Stop"
$BASE = Split-Path -Parent $MyInvocation.MyCommand.Path

function Write-Step($msg) {
    Write-Host ""
    Write-Host "========== $msg ==========" -ForegroundColor Cyan
}

function Write-OK($msg) {
    Write-Host "  [OK] $msg" -ForegroundColor Green
}

function Write-Warn($msg) {
    Write-Host "  [!] $msg" -ForegroundColor Yellow
}

function Write-Err($msg) {
    Write-Host "  [X] $msg" -ForegroundColor Red
}

# ============================================================
# 1. Python
# ============================================================
Write-Step "Step 1: Check Python"

$pythonExe = $null
$candidates = @(
    "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
    "python.exe",
    "py.exe"
)

foreach ($c in $candidates) {
    try {
        $ver = & $c --version 2>$null
        if ($LASTEXITCODE -eq 0 -and $ver -match "Python 3\.(\d+)") {
            $minor = [int]$Matches[1]
            if ($minor -ge 11) {
                $pythonExe = $c
                $pyVer = $ver.Trim()
                break
            }
        }
    } catch { }
}

if ($pythonExe) {
    Write-OK "Found $pyVer ($pythonExe)"
} else {
    Write-Warn "Python 3.11+ not found, downloading Python $PythonVersion"

    $installer = "$env:TEMP\python-$PythonVersion-amd64.exe"
    $url = "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-amd64.exe"

    Write-Host "  Download: $url"
    try {
        Invoke-WebRequest -Uri $url -OutFile $installer -UseBasicParsing
    } catch {
        Write-Err "Download failed: $_"
        Write-Host "  Please install Python 3.12+ manually: https://www.python.org/downloads/"
        exit 1
    }

    Write-Host "  Installing (silent mode, add to PATH)..."
    $installArgs = "/quiet", "InstallAllUsers=0", "PrependPath=1", "Include_pip=1"
    Start-Process -FilePath $installer -ArgumentList $installArgs -Wait

    $pythonExe = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
    if (-not (Test-Path $pythonExe)) {
        $pythonExe = "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe"
    }

    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "User") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "Machine")

    if (Test-Path $pythonExe) {
        $ver = & $pythonExe --version 2>$null
        Write-OK "Installed: $ver"
    } else {
        Write-Err "python.exe not found after install"
        exit 1
    }

    Remove-Item $installer -Force -ErrorAction SilentlyContinue
}

# ============================================================
# 2. pip packages: mitmproxy, pywin32
# ============================================================
Write-Step "Step 2: Install Python packages"

$packages = @("mitmproxy", "pywin32")
foreach ($pkg in $packages) {
    # pywin32 has no importable top-level module; win32gui is what we actually use
    $modName = $pkg.Replace("-", "_")
    if ($pkg -eq "pywin32") { $modName = "win32gui" }
    Write-Host "  Checking $pkg ..."
    $installed = & $pythonExe -c "import importlib; m=importlib.import_module('$modName'); print(getattr(m,'__version__','installed'))" 2>$null
    if ($LASTEXITCODE -eq 0) {
        Write-OK "$pkg installed ($installed)"
        continue
    }
    Write-Host "  Installing $pkg ..."
    & $pythonExe -m pip install $pkg --quiet
    # pip may report "already satisfied" based on metadata from a .pth-bridged
    # site-packages whose binary modules cannot actually load. Re-verify with
    # a real import; force a genuine reinstall into THIS interpreter if broken.
    & $pythonExe -c "import $modName" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Warn "pip claimed '$pkg' satisfied but import failed (.pth bridge?). Force reinstalling into this interpreter ..."
        & $pythonExe -m pip install $pkg --force-reinstall --no-deps --quiet
        & $pythonExe -c "import $modName" 2>$null
    }
    if ($LASTEXITCODE -eq 0) {
        Write-OK "$pkg installed and import verified"
    } else {
        Write-Err "$pkg install failed (import still broken after force reinstall)"
        exit 1
    }
}

Write-Host "  pywin32 post-install..."
# Only needed for COM registration; win32gui/win32api work without it.
# Native stderr + $ErrorActionPreference=Stop escalates tracebacks into
# terminating errors, so relax EAP around these calls.
$scriptsDir = Join-Path (Split-Path $pythonExe) "Scripts"
$postInstall = Join-Path $scriptsDir "pywin32_postinstall.py"
$prevEap = $ErrorActionPreference
$ErrorActionPreference = "Continue"
if (Test-Path $postInstall) {
    & $pythonExe $postInstall -install 2>$null
    if ($LASTEXITCODE -eq 0) {
        Write-OK "pywin32 post-install done"
    } else {
        Write-Warn "pywin32 post-install failed (COM registration only, not required by this tool)"
    }
} else {
    & $pythonExe -c "import pywin32_postinstall; pywin32_postinstall.install()" 2>$null
    Write-Warn "pywin32 post-install may need manual run"
}
$ErrorActionPreference = $prevEap

# ============================================================
# 3. mitmproxy CA certificate
# ============================================================
Write-Step "Step 3: Configure mitmproxy CA certificate"

$mitmproxyDir = Join-Path $env:USERPROFILE ".mitmproxy"
$caCert = Join-Path $mitmproxyDir "mitmproxy-ca-cert.cer"
$caCertPem = Join-Path $mitmproxyDir "mitmproxy-ca-cert.pem"

if (-not (Test-Path $caCert) -and -not (Test-Path $caCertPem)) {
    Write-Host "  First run mitmdump to generate CA cert..."

    # Write a temp Python script to start mitmdump briefly
    $tempPy = Join-Path $env:TEMP "gen_mitm_cert.py"
    $pyCode = @"
import sys
sys.argv = ['mitmdump', '-p', '18888']
from mitmproxy.tools.main import mitmdump
mitmdump()
"@
    $pyCode | Out-File -FilePath $tempPy -Encoding ascii -Force

    $proc = Start-Process -FilePath $pythonExe -ArgumentList $tempPy -PassThru -WindowStyle Hidden
    Start-Sleep -Seconds 5
    if ($proc -and -not $proc.HasExited) {
        $proc | Stop-Process -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 1
    Remove-Item $tempPy -Force -ErrorAction SilentlyContinue
}

if (Test-Path $caCert) {
    Write-Host "  CA cert: $caCert"
    $certCheck = certutil -store Root "mitmproxy" 2>$null
    if ($certCheck -match "mitmproxy") {
        Write-OK "CA cert already in system trust store"
    } else {
        Write-Host "  Importing CA cert to system trust store..."
        $result = certutil -addstore -f Root $caCert 2>&1
        if ($result -match "CertUtil.*success" -or $result -match "success") {
            Write-OK "CA cert imported successfully"
        } else {
            Write-Warn "Auto import may have failed. Please double-click $caCert to install manually to Trusted Root Certification Authorities"
        }
    }
} elseif (Test-Path $caCertPem) {
    Write-Host "  Found PEM cert, converting to CER..."

    $convPy = Join-Path $env:TEMP "conv_cert.py"
    $convCode = @"
import base64, os
pem_path = r'$caCertPem'
cer_path = os.path.join(os.path.dirname(pem_path), 'mitmproxy-ca-cert.cer')
with open(pem_path, 'r') as f:
    lines = f.read()
parts = lines.split('-----BEGIN CERTIFICATE-----')
for p in parts[1:]:
    b64 = p.split('-----END CERTIFICATE-----')[0].strip()
    der = base64.b64decode(b64)
    with open(cer_path, 'wb') as f:
        f.write(der)
    break
print('CER written:', cer_path)
"@
    $convCode | Out-File -FilePath $convPy -Encoding ascii -Force
    & $pythonExe $convPy 2>$null
    Remove-Item $convPy -Force -ErrorAction SilentlyContinue

    if (Test-Path $caCert) {
        certutil -addstore -f Root $caCert 2>&1 | Out-Null
        Write-OK "CA cert imported successfully"
    } else {
        Write-Warn "Conversion failed. Please run mitmdump manually first, then install the cert from $mitmproxyDir"
    }
} else {
    Write-Warn "CA cert not found in $mitmproxyDir"
    Write-Host "  Please run mitmdump manually, press Ctrl+C, then install cert from:"
    Write-Host "  $mitmproxyDir\mitmproxy-ca-cert.cer"
}

# ============================================================
# 4. Optional: Build exe
# ============================================================
if ($BuildExe) {
    Write-Step "Step 4: Build dianping_scraper.exe"

    $scraperPy = Join-Path $BASE "dianping_scraper.py"
    if (-not (Test-Path $scraperPy)) {
        Write-Err "dianping_scraper.py not found"
        exit 1
    }

    $hasPyInstaller = & $pythonExe -c "import PyInstaller; print(PyInstaller.__version__)" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  Installing PyInstaller..."
        & $pythonExe -m pip install pyinstaller --quiet
    }

    $buildDir = "D:\pyinstaller_build"
    $distDir = "D:\pyinstaller_dist"
    New-Item -ItemType Directory -Path $buildDir -Force | Out-Null
    New-Item -ItemType Directory -Path $distDir -Force | Out-Null

    $env:TEMP = $buildDir
    $env:TMP = $buildDir

    Write-Host "  Building..."
    & $pythonExe -m PyInstaller --onefile --console --name dianping_scraper `
        --hidden-import win32gui --hidden-import win32api --hidden-import win32con --hidden-import pywintypes `
        --distpath $distDir --workpath (Join-Path $buildDir "work") --specpath $buildDir `
        $scraperPy

    $exePath = Join-Path $distDir "dianping_scraper.exe"
    if (Test-Path $exePath) {
        $size = [math]::Round((Get-Item $exePath).Length / 1MB, 1)
        Write-OK "Build success: $exePath ($size MB)"
        Copy-Item $exePath (Join-Path $BASE "dianping_scraper.exe") -Force
        Write-OK "Copied to project directory"
    } else {
        Write-Err "Build failed"
        exit 1
    }
}

# ============================================================
# Done
# ============================================================
Write-Step "Setup complete"
Write-Host "  Python: $pythonExe"
Write-Host "  mitmproxy + pywin32: installed"
Write-Host "  CA cert: $mitmproxyDir"
Write-Host ""
Write-Host "  Subcommands:"
Write-Host "    dianping_scraper.exe check-env              verify environment"
Write-Host "    dianping_scraper.exe capture <name> <id>    capture reviews (needs WeChat UI)"
Write-Host "    dianping_scraper.exe export <id>            convert capture to CSV/JSON"
Write-Host ""
Write-Host "  Examples:"
Write-Host "    dianping_scraper.exe check-env --json"
Write-Host "    dianping_scraper.exe capture 'FengYu' 080501 --target 1400 --json"
Write-Host "    dianping_scraper.exe export 080501 --org-code 080501 --store-name 'FengYu' --format csv --json"
Write-Host ""
Write-Host "  Add --json for machine-readable single-line JSON on stdout (logs go to stderr)."
Write-Host "  See README_AGENT.md for the full calling contract."
