FROM python:3.11-slim@sha256:e88e9763f943ec1834f992a4b51e0f24500486803e8bc534e5767af9ea65f6ce

WORKDIR /app

RUN apt-get update && apt-get install -y \
    gcc \
    libpq-dev \
    openssh-client \
    openssl \
    && rm -rf /var/lib/apt/lists/*

# setuptools vendors wheel and jaraco.context in the base image; upgrading it replaces the vulnerable copies
RUN pip install --no-cache-dir --upgrade "setuptools>=84.0.0"

COPY requirements.txt requirements-dev.txt ./
# Test tools are only installed for the development overlay (INSTALL_DEV=1)
ARG INSTALL_DEV=0
RUN pip install --no-cache-dir -r requirements.txt \
    && if [ "$INSTALL_DEV" = "1" ]; then pip install --no-cache-dir -r requirements-dev.txt; fi

COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
