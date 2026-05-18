# Loop calling drain_queue until queue is empty
# Each iteration handles 1-2 items before rate limits kick in
$env:PATENT_DATA_DIR = "./data"
$env:PYTHONIOENCODING = "utf-8"

$max_iterations = 60
$iter = 0

while ($iter -lt $max_iterations) {
    $iter++
    Write-Host "=== Run $iter ===" -ForegroundColor Cyan

    # Reset stuck items
    python reset_queue.py 2>$null

    # Check if queue is empty
    $status = python -c "
import os; os.environ['PATENT_DATA_DIR'] = './data'
from src.patent_pipeline.database import PatentDatabase
db = PatentDatabase()
p = db.connection.execute(\"SELECT COUNT(*) FROM parse_queue WHERE status='pending'\").fetchone()[0]
d = db.connection.execute(\"SELECT COUNT(*) FROM parse_queue WHERE status='done'\").fetchone()[0]
r = db.connection.execute(\"SELECT COUNT(*) FROM reactions WHERE source='patent'\").fetchone()[0]
print(f'{p},{d},{r}')
db.close()
" 2>$null

    $parts = $status.Split(',')
    $pending = [int]$parts[0]
    $done = [int]$parts[1]
    $rxns = [int]$parts[2]
    Write-Host "  Pending=$pending Done=$done LLM-Rxns=$rxns"

    if ($pending -eq 0) {
        Write-Host "Queue empty! All done." -ForegroundColor Green
        break
    }

    # Run drain_queue (will process 1-3 items)
    python -u drain_queue.py 2>$null

    Start-Sleep -Seconds 3
}

Write-Host "Final status:"
python status.py 2>$null
