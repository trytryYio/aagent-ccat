param(
    [string]$Python = "python",
    [string]$ProjectRoot = (Get-Location).Path,
    [string]$BaseUrl = "http://127.0.0.1:8000",
    [ValidateSet("api", "orchestrator")]
    [string]$E2EMode = "api"
)

$ErrorActionPreference = "Stop"

$env:PYTHONPATH = $ProjectRoot
Set-Location $ProjectRoot

Write-Host "== RAG retrieval evaluation =="
& $Python "rag\scripts\evaluate_performance.py" `
    --dataset "rag\eval\datasets\rag_eval_dataset.jsonl"

Write-Host ""
Write-Host "== RAG end-to-end evaluation =="
& $Python "rag\scripts\evaluate_end_to_end.py" `
    --dataset "rag\eval\datasets\rag_e2e_dataset.jsonl" `
    --mode $E2EMode `
    --base-url $BaseUrl
