#!/bin/sh
# Export PORT if not set (Railway usually sets it, but this is a safety net)
export PORT=${PORT:-8000}
echo "Starting Uvicorn on port $PORT..."
exec uvicorn main:app --host 0.0.0.0 --port $PORT
