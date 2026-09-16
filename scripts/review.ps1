<#
.SYNOPSIS
    Try an agent's branch without disturbing your own checkout.

    .\scripts\review.ps1 list                 what is waiting to be reviewed
    .\scripts\review.ps1 diff   <branch>      what it changed, against main
    .\scripts\review.ps1 test   <branch>      run the tests on it
    .\scripts\review.ps1 run    <branch>      open the web app on it
    .\scripts\review.ps1 merge  <branch>      fast-forward main once you are happy
    .\scripts\review.ps1 clean  [branch]      remove the review copy

    Each branch is checked out into its own directory beside the repository, so
    your working copy keeps whatever you were doing and the two never fight over
    a file. They share one card database through MTGFISH_DATA_DIR - it is 100 MB
    and identical on every branch, so building one per review would be waste.
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)][ValidateSet('all', 'list', 'diff', 'test', 'run', 'merge', 'clean')][string]$Command = 'list',
    [Parameter(Position = 1)][string]$Branch,
    [int]$Port = 8010,
    [switch]$Full,      # test: the whole suite, parser included (~10 minutes)
    [switch]$AsIs,      # test the branch alone, without merging main into it
    [switch]$Quiet
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$reviewRoot = Join-Path (Split-Path -Parent $repo) 'mtgfish-review'
$dataDir = Join-Path $repo 'cache'
# How to type this script again, in whatever way the user just typed it.
$script = if ($PSCommandPath -and (Get-Location).Path -eq $repo) { '.\scripts
eview.ps1' } else { $PSCommandPath }

function Say($text, $colour = 'Cyan') { if (-not $Quiet) { Write-Host $text -ForegroundColor $colour } }

# git writes ordinary progress to stderr, which PowerShell would otherwise turn
# into a terminating error. Exit codes are what actually say whether it worked.
function Invoke-Git {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { & git @Arguments 2>&1 | ForEach-Object { "$_" } } finally { $ErrorActionPreference = $previous }
}

function Assert-Git($message) { if ($LASTEXITCODE -ne 0) { throw $message } }

function Resolve-Branch([string]$name) {
    if (-not $name) { throw "Which branch? Run: .\scripts\review.ps1 list" }
    $name = $name -replace '^origin/', ''
    Invoke-Git -C $repo show-ref --verify --quiet "refs/remotes/origin/$name" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "No branch 'origin/$name'. Run: .\scripts\review.ps1 list" }
    return $name
}

function Get-Worktree([string]$name) {
    $slug = $name -replace '[^A-Za-z0-9._-]', '-'
    $path = Join-Path $reviewRoot $slug
    if (-not (Test-Path $path)) {
        Say "Checking out $name into $path"
        Invoke-Git -C $repo worktree add --force --detach $path "origin/$name" | Out-Null
        Assert-Git "Could not check out origin/$name"
    } else {
        Say "Updating $path to origin/$name"
        Invoke-Git -C $path checkout --detach --force "origin/$name" | Out-Null
        Assert-Git "Could not update $path to origin/$name"
    }

    # What matters is the branch *merged into main*, which is what landing it
    # would produce - not the branch as the agent last saw it. A branch that
    # was fine when written can still break against what main has learned
    # since, and testing it alone would call that green.
    $behind = (Invoke-Git -C $repo rev-list --count "origin/$name..origin/main" | Select-Object -First 1)
    if ($AsIs) {
        Say "Testing the branch as it stands, $behind commit(s) behind main." 'Yellow'
    } elseif ("$behind".Trim() -ne '0') {
        Say "Merging main ($behind commit(s) it has not got) into the review copy"
        Invoke-Git -C $path merge --no-edit origin/main | Out-Null
        if ($LASTEXITCODE -ne 0) {
            Invoke-Git -C $path merge --abort | Out-Null
            throw ("origin/$name conflicts with main. Ask the agent to merge main and " +
                   "push again, or review it with -AsIs to see it on its own.")
        }
    }
    return $path
}

Invoke-Git -C $repo fetch origin --prune --quiet | Out-Null
# Pull-request heads, so "everything" includes PRs raised from branches this
# clone has never seen - including forks, which have no branch here at all.
Invoke-Git -C $repo fetch origin --quiet '+refs/pull/*/head:refs/remotes/origin/pr/*' | Out-Null

function Get-Candidates {
    $seen = @{}
    $out = @()
    foreach ($ref in @('refs/remotes/origin/pr', 'refs/remotes/origin/claude')) {
        foreach ($row in @(Invoke-Git -C $repo for-each-ref --sort=-committerdate `
                    --format='%(refname:short)|%(contents:subject)' $ref)) {
            $parts = "$row" -split '\|', 2
            $name = ($parts[0]).Trim() -replace '^origin/', ''
            $ahead = "$(Invoke-Git -C $repo rev-list --count "origin/main..origin/$name" | Select-Object -First 1)".Trim()
            # Nothing ahead of main is already merged, or stale. Either way
            # there is nothing left to judge.
            if ($ahead -eq '0') { continue }
            $sha = "$(Invoke-Git -C $repo rev-parse "origin/$name" | Select-Object -First 1)".Trim()
            if ($seen.ContainsKey($sha)) { continue }   # a PR and its branch are one thing
            $seen[$sha] = $true
            $out += [pscustomobject]@{ Name = $name; Subject = $parts[1]; Ahead = $ahead }
        }
    }
    return $out
}

switch ($Command) {
    'all' {
        $candidates = Get-Candidates
        if (-not $candidates) { Say "Nothing to review - no branch or PR is ahead of main." 'Green'; break }
        Say ("Reviewing {0}: each one merged with main, then tested." -f $candidates.Count)
        $results = @()
        foreach ($candidate in $candidates) {
            Write-Host ""
            Say ("=== {0}  ({1} commit(s) ahead)" -f $candidate.Name, $candidate.Ahead)
            Write-Host ("    {0}" -f $candidate.Subject) -ForegroundColor DarkGray
            $started = Get-Date
            $verdict = 'passed'
            $detail = ''
            try {
                $path = Get-Worktree $candidate.Name
            } catch {
                $results += [pscustomobject]@{ Name = $candidate.Name; Verdict = 'conflicts with main'; Detail = ''; Seconds = 0 }
                Say "    conflicts with main - not tested" 'Yellow'
                continue
            }
            $target = if ($Full) { @('tests') } else { @('tests/rules', 'tests/sim', 'tests/ui', 'tests/web', 'tests/data') }
            $env:MTGFISH_DATA_DIR = $dataDir
            $env:PYTHONPATH = $path
            Push-Location $path
            try {
                $previous = $ErrorActionPreference
                $ErrorActionPreference = 'Continue'
                $output = & python -m pytest @target -q -p no:cacheprovider 2>&1 | ForEach-Object { "$_" }
                $code = $LASTEXITCODE
                $ErrorActionPreference = $previous
            } finally { Pop-Location }
            $summary = ($output | Select-String -Pattern '^\d+ (passed|failed)|failed,|passed,' | Select-Object -Last 1)
            if ($code -ne 0) {
                $verdict = 'FAILED'
                $failures = @($output | Select-String -Pattern '^FAILED ' | ForEach-Object { ($_ -split ' ')[1] })
                $detail = ($failures | Select-Object -First 4) -join ', '
                Say ("    {0}" -f "$summary".Trim()) 'Red'
                foreach ($failure in $failures | Select-Object -First 6) { Write-Host "      $failure" -ForegroundColor Red }
            } else {
                Say ("    {0}" -f "$summary".Trim()) 'Green'
            }
            $results += [pscustomobject]@{
                Name = $candidate.Name; Verdict = $verdict; Detail = $detail
                Seconds = [int]((Get-Date) - $started).TotalSeconds
            }
        }
        Write-Host ""
        Say "Summary"
        foreach ($result in $results) {
            $colour = if ($result.Verdict -eq 'passed') { 'Green' } else { 'Red' }
            Write-Host ("  {0,-42} {1,-20} {2,4}s  {3}" -f $result.Name, $result.Verdict, $result.Seconds, $result.Detail) -ForegroundColor $colour
        }
        $good = @($results | Where-Object { $_.Verdict -eq 'passed' })
        if ($good) {
            Say ("`nReady to land: {0}" -f (($good.Name) -join ', '))
            Say ("  {0} merge <branch>   (or merge its PR on GitHub)" -f $script)
        }
    }

    'list' {
        Say "Branches waiting for review (newest first):"
        $rows = @(Invoke-Git -C $repo for-each-ref --sort=-committerdate `
                --format='%(refname:short)|%(committerdate:relative)|%(contents:subject)' `
                refs/remotes/origin/claude)
        if (-not $rows) { Say "  (none - no agent branches on the remote)" 'DarkGray' }
        foreach ($row in $rows) {
            $parts = $row -split '\|', 3
            $name = ($parts[0]).Trim() -replace '^origin/', ''
            $ahead = (Invoke-Git -C $repo rev-list --count "origin/main..origin/$name" | Select-Object -First 1)
            Write-Host ("  {0,-42} {1,-14} {2} commit(s) ahead" -f $name, $parts[1], "$ahead".Trim())
            Write-Host ("      {0}" -f $parts[2]) -ForegroundColor DarkGray
        }
        Say "`nThen: .\scripts\review.ps1 diff <branch>   (or test / run / merge)"
    }

    'diff' {
        $name = Resolve-Branch $Branch
        Say "What origin/$name changes, against main:"
        Invoke-Git -C $repo log --oneline --no-merges "origin/main..origin/$name"
        Write-Host ""
        Invoke-Git -C $repo diff --stat "origin/main...origin/$name"
    }

    'test' {
        $name = Resolve-Branch $Branch
        $path = Get-Worktree $name
        $target = if ($Full) { @('tests') } else { @('tests/rules', 'tests/sim', 'tests/ui', 'tests/web', 'tests/data') }
        Say "Running $(if ($Full) {'the whole suite'} else {'everything except the slow parser suite'}) on $name"
        $env:MTGFISH_DATA_DIR = $dataDir
        $env:PYTHONPATH = $path
        Push-Location $path
        try {
            $previous = $ErrorActionPreference
            $ErrorActionPreference = 'Continue'
            & python -m pytest @target -q -p no:cacheprovider
            $code = $LASTEXITCODE
            $ErrorActionPreference = $previous
        } finally { Pop-Location }
        if ($code -eq 0) { Say "`nGreen. .\scripts\review.ps1 run $name   to look at it." 'Green' }
        else { Say "`nFailures above - the branch is not ready." 'Red' }
    }

    'run' {
        $name = Resolve-Branch $Branch
        $path = Get-Worktree $name
        Say "Serving $name on http://127.0.0.1:$Port  (your own server on 8000 is untouched)"
        Say "Ctrl+C to stop."
        $env:MTGFISH_DATA_DIR = $dataDir
        $env:PYTHONPATH = $path
        Push-Location $path
        try {
            $previous = $ErrorActionPreference
            $ErrorActionPreference = 'Continue'
            & python -m mtgfish.web --port $Port
            $ErrorActionPreference = $previous
        } finally { Pop-Location }
    }

    'merge' {
        $name = Resolve-Branch $Branch
        $dirty = Invoke-Git -C $repo status --porcelain
        if ($dirty) { throw "Your working copy has uncommitted changes; commit or stash them first." }
        Say "Merging origin/$name into main"
        Invoke-Git -C $repo checkout main | Out-Null
        Assert-Git "Could not switch to main"
        Invoke-Git -C $repo merge --ff-only "origin/$name"
        if ($LASTEXITCODE -ne 0) {
            Say "Not a fast-forward: main has moved on. Ask the agent to rebase, or merge it on GitHub." 'Yellow'
        } else {
            Say "Merged. Push it with: git push" 'Green'
        }
    }

    'clean' {
        if ($Branch) {
            $slug = ($Branch -replace '^origin/', '') -replace '[^A-Za-z0-9._-]', '-'
            $path = Join-Path $reviewRoot $slug
            if (Test-Path $path) { Invoke-Git -C $repo worktree remove --force $path | Out-Null; Say "Removed $path" }
            else { Say "Nothing at $path" }
        } elseif (Test-Path $reviewRoot) {
            Get-ChildItem $reviewRoot -Directory | ForEach-Object {
                Invoke-Git -C $repo worktree remove --force $_.FullName | Out-Null
                Say "Removed $($_.FullName)"
            }
        }
        Invoke-Git -C $repo worktree prune | Out-Null
        Say "Review copies removed."
    }
}
