@echo off
chcp 65001 > nul
title no.dev client — Сборка (папка)

echo ==========================================
echo   Сборка no.dev client в папку
echo ==========================================
echo.

echo [1/4] Установка PyInstaller...
pip install --upgrade pyinstaller

echo.
echo [2/4] Очистка старых сборок...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo.
echo [3/4] Сборка...
pyinstaller --noconfirm --onedir --windowed ^
  --name "no.dev-client" ^
  --icon "icon.ico" ^
  --add-data "icon.ico;." ^
  main.py

echo.
echo [4/4] Копирование папок Zapret и tg-ws-proxy...
xcopy /E /I /Y "zapret-discord-youtube-1.10.2" "dist\no.dev-client\zapret-discord-youtube-1.10.2"
xcopy /E /I /Y "tg-ws-proxy-1.10.2" "dist\no.dev-client\tg-ws-proxy-1.10.2"

echo.
echo ==========================================
echo   Готово!
echo   Папка: dist\no.dev-client\
echo ==========================================
pause