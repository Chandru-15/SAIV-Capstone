FROM python:3.11-slim@sha256:db20d8506e2f15b6db39b40b6015ca46bae855dec6dd326d462cfe79ae972893

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
COPY requirements.lock.txt .
# Streamlit 1.30 requires packaging<24; newer base-image wheel requires >=24.
RUN pip install --no-cache-dir wheel==0.45.1 -r requirements.txt -c requirements.lock.txt

# Copy application code
COPY . .

# Expose port
EXPOSE 8501 9101

# Run the application
CMD ["streamlit", "run", "app/main.py", "--server.port=8501", "--server.address=0.0.0.0"]
