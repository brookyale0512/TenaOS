FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY TenaAgent/service/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY TenaAgent /var/www/TenaOS/TenaAgent
WORKDIR /var/www/TenaOS/TenaAgent/service

ENV TENA_AGENT_ROOT=/var/www/TenaOS/TenaAgent
ENV TENA_AGENT_SERVICE_HOST=0.0.0.0
ENV TENA_AGENT_SERVICE_PORT=8095

EXPOSE 8095

CMD ["python", "main.py"]
