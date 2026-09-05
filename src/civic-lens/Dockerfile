FROM python:3.12-slim

WORKDIR /app

# システムパッケージ
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Python依存
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# アプリ
COPY . .

EXPOSE 8080

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]