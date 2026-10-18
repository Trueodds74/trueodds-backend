# Use official Python slim image for smaller size & security
FROM python:3.12-slim

# Set working directory inside container
WORKDIR /app

# Install system dependencies required for psycopg2 (PostgreSQL driver)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first to leverage Docker layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code
COPY . .

# Expose port 8000 (Railway/Render default)
EXPOSE 8000

# Start the app using Uvicorn
