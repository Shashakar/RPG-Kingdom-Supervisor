param([Parameter(Mandatory = $true)][string]$OutputRoot)

$ErrorActionPreference = "Stop"
if (-not (Test-Path -LiteralPath $OutputRoot -PathType Container)) {
    throw "OutputRoot must be an existing directory"
}
$probe = Join-Path $OutputRoot ("unc-probe-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $probe | Out-Null

foreach ($runner in @("run-unity-tests.ps1", "run-unity-capture.ps1")) {
    & {
        $source = Join-Path $PSScriptRoot $runner
        $tokens = $null
        $parseErrors = $null
        $ast = [System.Management.Automation.Language.Parser]::ParseFile(
            $source, [ref]$tokens, [ref]$parseErrors)
        if ($parseErrors.Count -gt 0) { throw ($parseErrors | Out-String) }
        # Load function definitions only, never the runner's top-level Unity code.
        $functions = $ast.FindAll({
            param($node)
            $node -is [System.Management.Automation.Language.FunctionDefinitionAst]
        }, $true)
        $definitions = ($functions | ForEach-Object { $_.Extent.Text }) -join "`n"
        . ([scriptblock]::Create($definitions))
        $ProgressPath = Join-Path $probe "$runner.json"
        $RequestId = "unc-progress-probe"
        $script:ProgressSequence = 0
        foreach ($n in 1..3) {
            Write-ProgressState -Phase "probe_$n"
            $snapshot = "$ProgressPath.seq.$('{0:D10}' -f $n)"
            $published = if (Test-Path -LiteralPath $snapshot) { $snapshot } else { $ProgressPath }
            $result = Get-Content -LiteralPath $published -Raw | ConvertFrom-Json
            if ($result.requestId -ne $RequestId -or $result.sequence -ne $n -or $result.phase -ne "probe_$n") {
                throw "Progress verification failed for $runner at write $n"
            }
        }
        Write-Output "PASS: three consecutive progress publications: $source"
    }
}
Write-Output "Evidence: $probe"
