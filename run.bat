@echo off
chcp 65001 > nul
title Citation Network Builder

rem 1. Check Local AppData Python/bin
if exist "%LOCALAPPDATA%\Python\bin\python.exe" (
    "%LOCALAPPDATA%\Python\bin\python.exe" main.py %*
    goto end
)

rem 2. Check Local AppData Programs/Python
for /d %%i in ("%LOCALAPPDATA%\Programs\Python\Python*") do (
    if exist "%%i\python.exe" (
        "%%i\python.exe" main.py %*
        goto end
    )
)

rem 3. Check Program Files
for /d %%i in ("%ProgramFiles%\Python*") do (
    if exist "%%i\python.exe" (
        "%%i\python.exe" main.py %*
        goto end
    )
)

rem 4. Check Program Files (x86)
for /d %%i in ("%ProgramFiles(x86)%\Python*") do (
    if exist "%%i\python.exe" (
        "%%i\python.exe" main.py %*
        goto end
    )
)

rem 5. Check 'py' launcher
where py >nul 2>nul
if %errorlevel% equ 0 (
    py -0 >nul 2>nul
    if %errorlevel% equ 0 (
        py main.py %*
        goto end
    )
)

rem 6. Fallback to global python
python main.py %*
if %errorlevel% equ 0 goto end

rem 7. Error output
echo.
echo =====================================================================
echo  [ERROR] Python was not found. Please install Python 3.9 or higher.
echo =====================================================================
echo  1. If Python is not installed:
echo     - Download from https://python.org
echo     - IMPORTANT: Check "Add python.exe to PATH" during installation.
echo.
echo  2. If Python is installed but fails:
echo     - Go to Settings - Apps - Advanced App Settings - App Execution Aliases.
echo     - Turn OFF "App Installer" for python.exe and python3.exe.
echo =====================================================================
echo.
pause
exit

:end
pause
