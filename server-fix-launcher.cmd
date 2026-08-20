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
set OPHANIM_PHASE_6_ENABLED=1
set OPHANIM_PHASE_7_ENABLED=1
set OPHANIM_PHASE_7_DELAYED_EXECUTION=0
set OPHANIM_ALEXA_KITCHEN_DEVICE_ID=ac47606d23d7b56afc8687a71501cc89
set OPHANIM_ALEXA_MAIN_BEDROOM_DEVICE_ID=7d122025e842e9e0790bc7bb34d3070d
set OPHANIM_ALEXA_BEDROOM_DEVICE_ID=3deed799cc53eb1274c8a0b08bdab124
C:\Users\Marc\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe -m openjarvis.cli serve --host 127.0.0.1 --port 8000 > C:\Users\Marc\Documents\Projects\Ophanim\server-codex-fix.out.log 2> C:\Users\Marc\Documents\Projects\Ophanim\server-codex-fix.err.log
