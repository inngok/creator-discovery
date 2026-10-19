@echo off
set MODE=%1
if "%MODE%"=="" set MODE=hidden

if "%MODE%"=="visible" (
    echo [START] Chay che do VISIBLE - Mo cua so rieng...
    start "FastAPI Server" cmd /k "venv\Scripts\python.exe -m uvicorn main:app --host 0.0.0.0 --port 8000"
    start "Celery Worker" cmd /k "venv\Scripts\celery.exe -A app.workers.celery_app worker --loglevel=info --pool=threads --concurrency=1"
    echo [OK] Da mo 2 cua so FastAPI va Celery.
) else (
    echo [START] Chay che do HIDDEN - Chay ngam...
    venv\Scripts\python.exe start_hidden.py
)
pause
