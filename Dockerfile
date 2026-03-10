# SRS570 IOC Docker Image
# Multi-stage build for smaller final image

FROM python:3.11-slim as builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# Final stage
FROM python:3.11-slim

LABEL maintainer="SRS570 IOC Team"
LABEL description="EPICS IOC for SRS570 Current Preamplifier"
LABEL version="1.0.0"

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /root/.local /root/.local

# Make sure scripts in .local are usable
ENV PATH=/root/.local/bin:$PATH

# Copy application code
COPY srs570_ioc.py .
COPY config.py .
COPY config/ ./config/

# Create logs directory
RUN mkdir -p /app/logs

# Environment variables with defaults
ENV SRS570_IOC_IPADDR=0.0.0.0
ENV SRS570_CONNECTION_MODE=tcp
ENV SRS570_TCP_HOST=192.168.1.100
ENV SRS570_PORTS=1

# Expose EPICS CA ports (default range)
EXPOSE 5064-5071/tcp
EXPOSE 5064-5071/udp

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import socket; s=socket.socket(); s.settimeout(5); s.connect(('127.0.0.1', 5064)); s.close()" || exit 1

# Entry point script
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["python", "srs570_ioc.py"]
