# Operator-only discovery: never installs packages, opens an Editor, or modifies a project.
param(
    [string]$CliPath = "unity",
    [string]$ProjectPath = ""
)
$ErrorActionPreference = "Stop"

function Invoke-UnityCliProbe {
    param([string]$Executable, [string[]]$CommandArguments)
    try {
        $lines = @(& $Executable @CommandArguments 2>&1 | ForEach-Object { $_.ToString() })
        $code = $LASTEXITCODE
        return [pscustomobject]@{
            succeeded = ($code -eq 0)
            output = ($lines -join [Environment]::NewLine)
        }
    }
    catch {
        return [pscustomobject]@{
            succeeded = $false
            output = $_.Exception.Message
        }
    }
}

$result = [ordered]@{
    schemaVersion = 1
    status = "not-checked"
    cliPresent = $false
    cliVersion = $null
    cliVersionSucceeded = $false
    pipelineListSucceeded = $false
    projectCommandChecked = $false
    projectCommandSucceeded = $false
    projectPath = if ([string]::IsNullOrWhiteSpace($ProjectPath)) { $null } else { $ProjectPath }
    readyForProjectPilot = $false
}
$exitStatus = 0

try {
    $cli = Get-Command -Name $CliPath -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -eq $cli) {
        $result.status = "cli-missing"
        $exitStatus = 20
    }
    else {
        $result.cliPresent = $true
        $version = Invoke-UnityCliProbe -Executable $cli.Source -CommandArguments @("--version")
        $result.cliVersionSucceeded = $version.succeeded
        if ($version.succeeded) {
            $result.cliVersion = (($version.output -split "\r?\n" | Select-Object -First 1).Trim())
        }
        else {
            $result.status = "cli-version-failed"
            $exitStatus = 22
        }
        if ($exitStatus -eq 0) {
            $pipeline = Invoke-UnityCliProbe -Executable $cli.Source -CommandArguments @("pipeline", "list")
            $result.pipelineListSucceeded = $pipeline.succeeded
            if (-not $pipeline.succeeded) {
                $result.status = "pipeline-discovery-failed"
                $exitStatus = 23
            }
            elseif ([string]::IsNullOrWhiteSpace($ProjectPath)) {
                $result.status = "pipeline-listed-project-unchecked"
            }
            elseif (-not (Test-Path -LiteralPath $ProjectPath -PathType Container) -or
                    -not (Test-Path -LiteralPath (Join-Path $ProjectPath "ProjectSettings") -PathType Container) -or
                    -not (Test-Path -LiteralPath (Join-Path $ProjectPath "Assets") -PathType Container)) {
                $result.status = "invalid-project-path"
                $exitStatus = 21
            }
            else {
                $result.projectCommandChecked = $true
                $commands = Invoke-UnityCliProbe -Executable $cli.Source -CommandArguments @(
                    "command", "--project-path", $ProjectPath
                )
                $result.projectCommandSucceeded = $commands.succeeded
                if ($commands.succeeded) {
                    $result.status = "project-command-available"
                    $result.readyForProjectPilot = $true
                }
                else {
                    $result.status = "project-command-unavailable"
                    $exitStatus = 24
                }
            }
        }
    }
}
catch {
    $result.status = "probe-error"
    $exitStatus = 25
}

# Never emit raw CLI stdout/stderr in JSON; it may contain local paths or secrets.
$result | ConvertTo-Json -Depth 3 -Compress | Write-Output
exit $exitStatus
