FROM python:3.11-bookworm

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    unixodbc \
    unixodbc-dev \
    wget \
    gnupg \
    apt-transport-https \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN apt-get update && apt-get install -y curl gnupg2 apt-transport-https ca-certificates \
    && curl https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/microsoft-prod.gpg] https://packages.microsoft.com/debian/12/prod bookworm main" > /etc/apt/sources.list.d/mssql-release.list \
    && apt-get update \
    && ACCEPT_EULA=Y apt-get install -y msodbcsql17 unixodbc-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
