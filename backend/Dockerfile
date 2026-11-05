FROM python:3.12-slim

# Create non-root user for security
RUN groupadd -r appuser && useradd -r -g appuser -d /app -s /sbin/nologin appuser

WORKDIR /app

# Install dependencies first for better layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Switch to non-root user
RUN chown -R appuser:appuser /app
USER appuser

# Render assigns a dynamic PORT; default to 8000 for local Docker runs
ENV PORT=8000
EXPOSE ${PORT}

# Use gunicorn with uvicorn workers for production
# Render sets PORT dynamically; gunicorn binds to 0.0.0.0:$PORT
CMD gunicorn app.main:app \
    --worker-class uvicorn.workers.UvicornWorker \
    --bind 0.0.0.0:${PORT} \
    --workers 1 \
    --timeout 120 \
    --access-logfile - \
    --error-logfile -
