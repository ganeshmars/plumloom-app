FROM python:3.11-slim

# Arguments for user and group
ARG APP_USER=appuser
ARG APP_GROUP=appuser
ARG UID=1000
ARG GID=1000

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/home/${APP_USER}/.local/bin:${PATH}"

# Install system dependencies
# Added postgresql-client for psql/createdb in start.sh
# procps is for pgrep
RUN apt-get update && apt-get install -y --no-install-recommends \
    netcat-openbsd \
    build-essential \
    python3-dev \
    procps \
    postgresql-client \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Create a non-root user and group
RUN groupadd -g ${GID} ${APP_GROUP} \
    && useradd -u ${UID} -g ${APP_GROUP} -s /bin/bash -m -d /home/${APP_USER} ${APP_USER}

# Set working directory
WORKDIR /app

# Create necessary directories and assign ownership
RUN mkdir -p /tmp/flower /app/logs \
    && chown -R ${APP_USER}:${APP_GROUP} /app /tmp/flower /app/logs /home/${APP_USER}

# Copy requirements first to leverage Docker cache
COPY --chown=${APP_USER}:${APP_GROUP} requirements.txt .

# Switch to non-root user
USER ${APP_USER}

# Install build dependencies for pip
RUN pip install --no-cache-dir --upgrade pip setuptools wheel

# Install Python dependencies as the non-root user
# This will install packages potentially into user's site-packages if not in a venv
# Or system-wide if Python is configured for it (slim images often behave this way)
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code as non-root user
# This ensures app files are owned by appuser
COPY --chown=${APP_USER}:${APP_GROUP} . .

# Make startup script executable
RUN chmod +x /app/start.sh

# Expose port (documentation, not strictly necessary for docker-compose)
EXPOSE 5000

# Use the startup script as the entrypoint
CMD ["/app/start.sh"]