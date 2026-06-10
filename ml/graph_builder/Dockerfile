FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir \
    fastapi==0.111.0 \
    uvicorn[standard]==0.29.0 \
    httpx==0.27.0 \
    networkx==3.3 \
    structlog==24.1.0 \
    prometheus-client==0.20.0

COPY graph_builder.py .

EXPOSE 8000
CMD ["uvicorn", "graph_builder:gb_app", "--host", "0.0.0.0", "--port", "8000"]
