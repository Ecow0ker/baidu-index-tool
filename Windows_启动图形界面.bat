@echo off
setlocal
set "ROOT=%~dp0"
set "PYTHONPATH=%ROOT%src;%PYTHONPATH%"
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 -m baidu_index_tool.webview_gui
) else (
  python -m baidu_index_tool.webview_gui
)
if errorlevel 1 pause
