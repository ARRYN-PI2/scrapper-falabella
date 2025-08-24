# Dockerfile
FROM python:3.11-slim

# Evita prompts de apt
ENV DEBIAN_FRONTEND=noninteractive

# Dep libs (certs, tzdata, locales opcionales, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates tzdata curl unzip \
    && rm -rf /var/lib/apt/lists/*

# Directorio de trabajo
WORKDIR /app

# Copiamos requirements primero para mejor caché
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiamos tu código
COPY scrape_falabella_all.py ./scrape_falabella_all.py

# Por defecto ejecuta tu script
CMD ["python", "scrape_falabella_all.py"]
