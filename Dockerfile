FROM python:3.11-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
# Render обычно отдаёт переменную $PORT, но на всякий случай дефолт 10000
CMD uvicorn forward_bot_webhook:app --host 0.0.0.0 --port ${PORT:-10000}
