@echo off
echo Starting FastAPI Server...
start "FastAPI Server" cmd /k "venv\Scripts\python.exe -m uvicorn main:app --host 0.0.0.0 --port 8001"

echo Starting Celery Worker...
start "Celery Worker" cmd /k "venv\Scripts\celery.exe -A app.workers.celery_app worker --loglevel=info --pool=threads --concurrency=1"

echo Services started in new windows!
