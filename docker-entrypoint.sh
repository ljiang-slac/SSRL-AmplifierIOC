#!/bin/bash
set -e

# SRS570 IOC Docker Entrypoint Script

echo "========================================"
echo "SRS570 IOC Docker Container"
echo "========================================"

# Default values
CONNECTION_MODE=${SRS570_CONNECTION_MODE:-tcp}
TCP_HOST=${SRS570_TCP_HOST:-192.168.1.100}
PORTS=${SRS570_PORTS:-1}

echo "Configuration:"
echo "  Connection Mode: $CONNECTION_MODE"
echo "  TCP Host: $TCP_HOST"
echo "  Ports: $PORTS"
echo "========================================"

# Build command arguments
ARGS="-p $PORTS --mode $CONNECTION_MODE"

if [ "$CONNECTION_MODE" = "tcp" ]; then
    ARGS="$ARGS --tcp-host $TCP_HOST"
fi

# Check if custom config file is mounted
if [ -f "/app/config/custom_config.json" ]; then
    echo "Using custom configuration file"
    ARGS="$ARGS --config /app/config/custom_config.json"
fi

echo "Starting IOC with args: $ARGS"
echo "========================================"

# Execute the main command
exec "$@" $ARGS
