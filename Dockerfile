FROM python:3.12-slim

WORKDIR /app

# Install system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY . .

# Create data directories
RUN mkdir -p orders sessions

EXPOSE 8080 8081

# Default: dashboard on 8081
# Override CMD to run the Twilio server on 8080
CMD ["python", "main.py", "dashboard", "--port", "8081"]
