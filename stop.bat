@echo off
echo Dang tim va tieu diet cac tien trinh Uvicorn va Celery bi treo...
taskkill /F /IM uvicorn.exe /T 2>nul
taskkill /F /IM celery.exe /T 2>nul

echo.
echo Kiem tra tien trinh dang chay tren port 8001...
for /f "tokens=5" %%a in ('netstat -aon ^| find ":8001" ^| find "LISTENING"') do (
    echo Phat hien tien trinh an PID: %%a dang giu port 8001. Dang kill...
    taskkill /F /PID %%a 2>nul
)

echo.
echo Da don dep sach se! Ban co the chay lai start.bat ngay bay gio.
pause
