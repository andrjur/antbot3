@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
set PYTHONLEGACYWINDOWSSTDIO=utf-8

echo ========================================
echo Starting AntBot Docker Environment
echo ========================================
echo.

REM Проверяем Docker
docker version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Docker Desktop is not running!
    echo Please start Docker Desktop first.
    pause
    exit /b 1
)

REM Проверяем .env
if not exist .env (
    echo Creating .env file...
    (
        echo BOT_TOKEN=7473862113:AAFChEuDcqHA19dQPfbrO8L5MHOvt6HPi2M
        echo ADMIN_IDS=182643037,954230772
        echo ADMIN_GROUP_ID=-1002591981307
        echo WEBHOOK_HOST=http://localhost:8080
        echo WEBHOOK_SECRET_PATH=sadfasdfmy6777777mmmmh_a7b2c9z
        echo WEBHOOK_SECRET_TOKEN=r3324cret_tasdfasdfasdfk_check_x8y1w5
        echo WEB_SERVER_PORT=8080
        echo N8N_HOMEWORK_CHECK_URL=https://n8n.indikov.ru/webhook/aa46a723-619e-42e9-8e51-49ba51813718
        echo N8N_ASK_EXPERT_URL=https://n8n.indikov.ru/webhook/83c1f9c5-7833-49c2-9122-22efe590c793
        echo N8N_WEBHOOK_SECRET=S3222233221532My
        echo N8N_DOMAIN=https://n8n.indikov.ru/
        echo N8N_CALLBACK_SECRET=500
    ) > .env
    echo .env file created with default settings.
)

echo.
echo === Building and starting services ===
docker-compose up --build -d

if errorlevel 1 (
    echo ERROR: Failed to start containers!
    pause
    exit /b 1
)

echo.
echo === Services status ===
docker-compose ps
echo.
echo === Access URLs ===
echo Bot webhook: http://localhost:8080
echo Prometheus: http://localhost:9090
echo Grafana: http://localhost:3000 (admin/admin123)
echo Alertmanager: http://localhost:9093
echo.
echo === Bot logs (Ctrl+C to stop) ===
timeout /t 3 >nul
docker-compose logs -f bot