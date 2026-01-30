FROM python:3.10-slim

# Install system dependencies
# Added libxml2-dev and libxslt-dev for lxml support
RUN apt-get update && apt-get install -y \
    potrace \
    ffmpeg \
    libsm6 \
    libxext6 \
    build-essential \
    libxml2-dev \
    libxslt-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

EXPOSE 8501

CMD ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0"]