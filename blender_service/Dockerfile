FROM python:3.10-slim

# Install system dependencies required for Blender
RUN apt-get update && apt-get install -y \
    wget \
    xz-utils \
    libxi6 \
    libxrender1 \
    libxxf86vm1 \
    libxfixes3 \
    libxcursor1 \
    libgl1 \
    libdbus-1-3 \
    libxkbcommon0 \
    libsm6 \
    && rm -rf /var/lib/apt/lists/*

# Download and install Blender 4.0.0
# Using a fixed version for stability
WORKDIR /usr/local
RUN wget -q https://download.blender.org/release/Blender4.0/blender-4.0.0-linux-x64.tar.xz \
    && tar -xf blender-4.0.0-linux-x64.tar.xz \
    && mv blender-4.0.0-linux-x64 blender \
    && rm blender-4.0.0-linux-x64.tar.xz

# Add blender to PATH
ENV PATH="/usr/local/blender:$PATH"

# Install Python dependencies within Blender's bundled Python
# Blender 4.0 uses Python 3.10
RUN /usr/local/blender/4.0/python/bin/python3.10 -m ensurepip
RUN /usr/local/blender/4.0/python/bin/python3.10 -m pip install flask requests

# Install OCP in system python for isolated conversion
RUN pip install cadquery-ocp

# Set work directory
WORKDIR /app

# Copy server script
COPY server.py .
COPY repair.py .
COPY convert.py .
COPY step_export.py .
COPY connectors.py .
COPY dovetail.py .
COPY scripts/ /app/scripts/

# Create a volume mount point for media (shared with Django)
RUN mkdir -p /app/media

# Environment variables
ENV BLENDER_USER_SCRIPTS=/app/scripts

# Expose port
EXPOSE 8081

# Command to calculate or render
# We run blender in background mode (-b), executing the python script (-P)
CMD ["blender", "-b", "-P", "server.py"]
