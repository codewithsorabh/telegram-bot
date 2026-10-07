FROM python:3.11-slim

# Prevent Python from writing .pyc files & enable unbuffered logs
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Kolkata

WORKDIR /app

# Install system dependencies needed for compiling C-extensions (psycopg2, cryptg)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    git \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

# Install Python requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY . .

# Default command runs the telegram bot, can be overridden with:
#   docker run <image> python ./bot/bot.py
#   docker run <image> python ./userbot/main.py
CMD ["python", "./bot/bot.py"]