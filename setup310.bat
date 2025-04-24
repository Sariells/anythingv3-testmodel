@echo off

:: Получаем путь к директории .bat файла
set SCRIPT_DIR=%~dp0

:: Переключаем рабочую директорию на директорию .bat файла
cd /d "%SCRIPT_DIR%"

:: Проверка прав администратора
openfiles >nul 2>nul
if %errorlevel% NEQ 0 (
    echo Please run this script as Administrator.
    pause
    exit /b 1
)

:: Вывод текущей рабочей директории
echo Current directory: %cd%

:: Проверка на наличие Python
echo Checking for Python...
python --version
IF %ERRORLEVEL% NEQ 0 (
    echo Python is not installed. Please install Python 3.10 or higher.
    pause
    exit /b 1
)

:: Проверка на существование файла requirements.txt
if not exist "requirements.txt" (
    echo requirements.txt file not found in the project directory: %cd%
    pause
    exit /b 1
)

:: Создание виртуального окружения
echo Creating virtual environment...
if exist venv (
    echo Virtual environment already exists. Deleting it...
    rmdir /s /q venv
)

python -m venv venv

:: Активация виртуального окружения
echo Activating virtual environment...
call venv\Scripts\activate.bat
IF %ERRORLEVEL% NEQ 0 (
    echo Failed to activate the virtual environment.
    pause
    exit /b 1
)

:: Обновление pip
echo Upgrading pip...
python -m pip install --upgrade pip
IF %ERRORLEVEL% NEQ 0 (
    echo Failed to upgrade pip.
    pause
    exit /b 1
)

:: Установка зависимостей из requirements.txt
echo Installing dependencies...
pip install -r requirements.txt
IF %ERRORLEVEL% NEQ 0 (
    echo Failed to install dependencies. Please check the error messages.
    pause
    exit /b 1
)

:: Запуск твоего скрипта launch.py
echo Running the launch script...
python launch.py
IF %ERRORLEVEL% NEQ 0 (
    echo Failed to run launch.py. Please check the error messages.
    pause
    exit /b 1
)

:: Завершение
echo Setup completed successfully.
pause
