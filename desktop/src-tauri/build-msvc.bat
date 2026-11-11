@echo off
rem Runs cargo/tauri inside a proper MSVC environment (fixes linker PATH shadowing in git-bash).
call "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat"
cd /d C:\ai\repos\home-media\desktop\src-tauri
cargo %*
