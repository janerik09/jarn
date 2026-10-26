FROM python:3.12-slim@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704

WORKDIR /app

RUN apt-get update && apt-get install -y \
    gcc \
    libpq-dev \
    openssh-client \
    openssl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-dev.txt ./
# Test tools are only installed for the development overlay (INSTALL_DEV=1)
ARG INSTALL_DEV=0
RUN pip install --no-cache-dir -r requirements.txt \
    && if [ "$INSTALL_DEV" = "1" ]; then pip install --no-cache-dir -r requirements-dev.txt; fi
# The app does not need the packaging tools at run time. They bundle their own copies of urllib3 and msgpack, which
# lag behind security fixes, so they are removed from the final image. Test tools must be installed at build time.
RUN pip uninstall -y pip setuptools wheel 2>/dev/null; python -c "import uvicorn, celery, sqlalchemy, paramiko, jwt, ldap3; print('runtime imports ok')"

COPY . .

# Run as an unprivileged user. uid 1000 matches the owner of the host folders the app writes to (./tls, ./backups)
RUN groupadd --gid 1000 app && useradd --uid 1000 --gid app --no-create-home --shell /usr/sbin/nologin app \
    && chown -R root:root /app

USER app

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
