@echo off
echo ========================================================
echo        KHOI DONG HE THONG INFLUENCER MATCH CRAWLER      
echo ========================================================
echo.

echo [1] Dang bat API Server (FastAPI)...
start "1. FastAPI Server" cmd /k "call venv\Scripts\activate.bat && uvicorn main:app --reload"

echo [2] Dang bat Celery Worker (Doi cong nhan)...
start "2. Celery Worker" cmd /k "call venv\Scripts\activate.bat && celery -A app.workers.celery_app worker --loglevel=info --pool=threads"

echo [3] Dang khoi dong Discovery Bot (Thang di san)...
:: Đợi 5 giây để Celery khởi động xong rồi mới ném task vào
timeout /t 5 /nobreak > nul
start "3. Discovery Bot" cmd /k "call venv\Scripts\activate.bat && python app/scripts/discovery_bot.py"

echo.
echo ========================================================
echo Hoan thanh! Ban se thay 3 cua so mau den hien len:
echo - Cua so 1: API Server (Ban dung tat no, de chay ngam)
echo - Cua so 2: Celery Worker (Ban dung tat no, de chay ngam)
echo - Cua so 3: Discovery Bot (Dang mo trinh duyet di quet)
echo ========================================================
echo.
pause
