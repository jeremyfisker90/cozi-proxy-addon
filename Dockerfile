FROM python:3.11-slim

WORKDIR /cozi_proxy

COPY cozi_proxy/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY cozi_proxy/server.py .

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "5000"]




