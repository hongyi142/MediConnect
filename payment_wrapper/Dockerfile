# Use a lightweight Python base image
FROM python:3.10-slim

# Set working directory
WORKDIR /app

# Ensure logs are not buffered (critical for Docker logs)
ENV PYTHONUNBUFFERED=1

# Copy requirements first to leverage Docker layer caching
COPY requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code
COPY . .

# Run the Flask app
CMD ["python", "app.py"]
