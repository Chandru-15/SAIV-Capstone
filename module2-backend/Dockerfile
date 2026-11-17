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
RUN pip install --no-cache-dir -r requirements.txt -c requirements.lock.txt

# Copy application code
COPY . .

# Expose port
EXPOSE 8000

# Run the application.
#
# --no-proxy-headers is deliberate. Uvicorn's ProxyHeadersMiddleware is ON by
# default and REWRITES scope["client"] from X-Forwarded-For before the app sees
# it, so request.client.host would itself be a caller-supplied value. Worse,
# with forwarded_allow_ips="*" -- the usual container setting -- it returns the
# LEFTMOST entry, which is purely whatever the caller wrote.
#
# Disabling it keeps request.client as the real peer and leaves TRUSTED_PROXY_IPS
# (see dependencies.client_ip) the single place proxy trust is configured.
# See API-NOTES.md section 8.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--no-proxy-headers"]
