# Official Playwright image: Chromium plus every system library it needs.
# Keep the tag in step with the playwright pin in requirements.txt.
FROM mcr.microsoft.com/playwright/python:v1.58.0-noble

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PLAYWRIGHT_PREINSTALLED=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --break-system-packages -r requirements.txt
COPY . .

CMD ["python3", "server.py"]
