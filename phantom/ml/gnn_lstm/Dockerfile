FROM python:3.12-slim

WORKDIR /app

# requirements.txt is at ml/requirements.txt; build context is ./ml
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY gnn_lstm/model.py gnn_lstm/serve.py ./
RUN mkdir -p /models

EXPOSE 8001
CMD ["uvicorn", "serve:app", "--host", "0.0.0.0", "--port", "8001"]
