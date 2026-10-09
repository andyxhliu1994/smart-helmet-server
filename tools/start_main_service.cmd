@echo off
setlocal

cd /d "%~dp0\.."

if not defined HELMET_PYTHON (
  set "HELMET_PYTHON=C:\Users\ZYZL-GBHW\AppData\Local\Programs\Python\Python314\python.exe"
)

"%HELMET_PYTHON%" -m uvicorn app.main:app --host 0.0.0.0 --port 18787 1> uvicorn.out.log 2> uvicorn.err.log
