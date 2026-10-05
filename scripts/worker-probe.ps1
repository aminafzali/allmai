# AllMai worker probe: restart the supervisor task only when the worker is
# really gone or wedged (anti-flap: two consecutive bad reads required).
# Run every 5 minutes via: schtasks /Create /TN "AllMaiWorkerProbe" /TR
#   "powershell -NoProfile -ExecutionPolicy Bypass -File G:\MyProjects\allmai\scripts\worker-probe.ps1"
#   /SC MINUTE /MO 5 /RL HIGHEST /F
# Wedged = a source stuck in processing longer than timeout+slack while no
# celery process exists. A live worker grinding a big book is left alone.

$ErrorActionPreference = "SilentlyContinue"
$stateFile = "C:\Users\novin\AppData\Local\Temp\allmai-probe-state.txt"

function Get-WorkerAlive {
    return $null -ne (Get-Process celery -ErrorAction SilentlyContinue | Select-Object -First 1)
}

# DB check needs no secrets: RLS hides everything without GUCs, so we only
# ask "does the API see a live worker" via the local health endpoint.
$apiAlive = $false
try {
    $h = Invoke-RestMethod "http://127.0.0.1:8000/health" -TimeoutSec 10
    $apiAlive = ($h.status -eq "ok")
} catch { }

$workerAlive = Get-WorkerAlive
$bad = (-not $workerAlive)
$prev = 0
try { $prev = [int](Get-Content $stateFile) } catch { }
if ($bad) { $prev++ } else { $prev = 0 }
try { Set-Content $stateFile "$prev" } catch { }

if ($prev -ge 2) {
    $msg = "$(Get-Date -Format s) probe: worker missing twice in a row (api=$apiAlive), starting supervisor task"
    try { Add-Content "G:\MyProjects\allmai\.probe.log" $msg } catch { }
    try { Start-ScheduledTask -TaskName "AllMaiWorker" } catch { }
    try { Set-Content $stateFile "0" } catch { }
}
