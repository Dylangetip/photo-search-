FROM python:3.11-slim

WORKDIR /srv/ringfinder

# CPU-only torch wheels keep the image small
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu \
        torch==2.5.1 torchvision==0.20.1

COPY requirements.txt .
RUN grep -v -E '^torch(vision)?==' requirements.txt > /tmp/reqs.txt \
    && pip install --no-cache-dir -r /tmp/reqs.txt

COPY app ./app
COPY static ./static

# Model weights are downloaded on first run and cached in /data/models
# (HF_HOME / U2NET_HOME are pointed there by app/config.py).
EXPOSE 8420
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8420"]
