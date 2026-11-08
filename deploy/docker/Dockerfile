# 盤中停損監控（Docker）。在專案根目錄：
#   docker build -f deploy/docker/Dockerfile -t tw-stock-monitor .
#   docker run -d --restart unless-stopped --env-file .env --name tw-stock-monitor tw-stock-monitor
FROM python:3.12-slim
ENV TZ=Asia/Taipei PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml ./
COPY engine ./engine
COPY config ./config
COPY db ./db
RUN pip install --no-cache-dir -e .   # editable：設定檔路徑以專案根目錄為準
CMD ["python", "-m", "engine.cli", "monitor"]
