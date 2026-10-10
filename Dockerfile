FROM python:3.12-slim

ARG INSTALL_FFMPEG=false
WORKDIR /app

# Install ffmpeg conditionally
RUN if [ "$INSTALL_FFMPEG" = "true" ]; then \
    apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*; \
    fi

# Copy requirements and install them
COPY requirements.txt /app
RUN pip install --no-cache-dir -r requirements.txt

# Copy the app directory
COPY app/ /app

# Run as non-root
RUN useradd --create-home --shell /usr/sbin/nologin appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 5050

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,os;urllib.request.urlopen('http://127.0.0.1:%s/health' % os.getenv('PORT','5050'),timeout=4)"

# Command to run the server
CMD ["python", "/app/server.py"]
