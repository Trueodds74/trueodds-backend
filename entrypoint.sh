#!/bin/sh
# Force PORT to be a valid integer
if [ -z "$PORT" ]; then
    export PORT=8000
fi
echo "=== STARTING TRUEODDS BACKEND ON PORT $PORT ==="
exec uvicorn main:app --host 0.0.0.0 --port "$PORT"
