@echo off
setlocal

cd /d "%~dp0\.."

if not defined HELMET_PYTHON (
  set "HELMET_PYTHON=C:\Users\ZYZL-GBHW\AppData\Local\Programs\Python\Python314\python.exe"
)

"%HELMET_PYTHON%" tools\watch_main_service.py --restart 1> service_watch.out.log 2> service_watch.err.log
