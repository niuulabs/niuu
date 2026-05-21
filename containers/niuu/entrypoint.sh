#!/bin/bash
set -e

# Niuu Entrypoint Script
# Starts the unified Python control-plane host

echo "=== Niuu Control Plane ==="

echo "=== Starting Niuu Service ==="
exec uvicorn niuu.main:app --host 0.0.0.0 --port 8082
