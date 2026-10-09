FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY barcodescan/ ./barcodescan/
COPY job.py .

ENTRYPOINT ["python", "/app/job.py"]
