# Use Python 3.11 slim image as base
FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV DJANGO_SETTINGS_MODULE=modelfoundry.settings
ENV DJANGO_ALLOWED_HOSTS=*

# Set work directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    git \
    netcat-traditional \
    openscad \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
# Install Python dependencies
COPY requirements.txt .
COPY dependencies/ dependencies/
RUN pip install --no-cache-dir -r requirements.txt --find-links dependencies/

# Copy project files
COPY . .

# Copy and set permissions for entrypoint
COPY entrypoint.sh /entrypoint.sh
RUN sed -i 's/\r$//' /entrypoint.sh && chmod +x /entrypoint.sh

# Create necessary directories with proper permissions
RUN mkdir -p /app/media /app/staticfiles /app/logs \
    && chmod -R 777 /app/media /app/staticfiles /app/logs

# Expose port 8000
EXPOSE 8000

# Set entrypoint
ENTRYPOINT ["/entrypoint.sh"]

# Command to run the application using Gunicorn for production concurrency
# We use 1 worker with 3 threads so that the in-memory Forge Module Registry singleton is shared across all concurrent requests
CMD ["gunicorn", "modelfoundry.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "1", "--threads", "3", "--timeout", "300"] 