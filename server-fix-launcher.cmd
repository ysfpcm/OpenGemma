@echo off
cd /d C:\Users\Marc\Documents\Projects\Ophanim
set PYTHONPATH=C:\Users\Marc\Documents\Projects\Ophanim\src;C:\Users\Marc\Documents\Projects\Ophanim\.venv\Lib\site-packages
set USERPROFILE=C:\Users\Marc
set HOME=C:\Users\Marc
set OPENJARVIS_HOME=C:\Users\Marc\.openjarvis
set OPENJARVIS_CONFIG=C:\Users\Marc\Documents\Projects\Ophanim\config-runtime.toml
set JARVIS_NUM_CTX=8192
set OPHANIM_TRAFFIC_SNAPSHOT_PATH=C:\Users\Marc\.openjarvis\shared\commute-traffic.json
set OPHANIM_TRAFFIC_FILE_INTERVAL_SECONDS=30
C:\Users\Marc\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe -m openjarvis.cli serve --host 127.0.0.1 --port 8000 > C:\Users\Marc\Documents\Projects\Ophanim\server-codex-fix.out.log 2> C:\Users\Marc\Documents\Projects\Ophanim\server-codex-fix.err.log
