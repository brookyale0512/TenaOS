FROM python:3.12-slim

WORKDIR /app

COPY cds/service/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY cds /var/www/tenaos/cds
WORKDIR /var/www/tenaos/cds/service

ENV CDS_ROOT=/var/www/tenaos/cds
ENV CDS_SERVICE_HOST=0.0.0.0
ENV CDS_SERVICE_PORT=8095

EXPOSE 8095

CMD ["python", "main.py"]
