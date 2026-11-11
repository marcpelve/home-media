@echo off
rem Runs `tauri build` inside a proper MSVC environment (fixes linker PATH shadowing in git-bash).
rem Produces the release desktop exe + bundles the home-media sidecar + NSIS installer.
call "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat"
cd /d C:\ai\repos\home-media-trakt\desktop
rem Ship the PyInstaller one-dir backend as a Tauri resource (leaf folder, no '..').
rem Never bundle runtime data (DB/tokens) or .env - those live in %LOCALAPPDATA%
rem at runtime and are set via the in-app Settings tab.
if exist ..\backend\dist\home-media\data rmdir /s /q ..\backend\dist\home-media\data
if exist ..\backend\dist\home-media\.env del /f /q ..\backend\dist\home-media\.env
if exist src-tauri\resources\home-media rmdir /s /q src-tauri\resources\home-media
xcopy /E /I /Y ..\backend\dist\home-media src-tauri\resources\home-media >nul
tauri %*
