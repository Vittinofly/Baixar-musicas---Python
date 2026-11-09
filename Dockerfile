# Imagem de produção do YT Downloader (Linux): Python + FFmpeg + Node.js + Waitress.
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PATH=/opt/venv/bin:$PATH \
    YTD_ENV=production \
    YTD_HOST=0.0.0.0 \
    YTD_PORT=8000 \
    YTD_DATA_DIR=/data \
    YTD_BEHIND_PROXY=1

# FFmpeg (conversão), Node.js (desafio JavaScript do YouTube) e tini (encerra processos filhos direito)
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg nodejs ca-certificates tini \
 && rm -rf /var/lib/apt/lists/*

# Usuário sem privilégios; o venv pertence a ele para a atualização automática do yt-dlp funcionar.
RUN useradd --create-home --uid 1000 app \
 && python -m venv /opt/venv && chown -R app:app /opt/venv \
 && mkdir /data && chown app:app /data

WORKDIR /app
COPY --chown=app:app requirements.txt .
USER app
RUN pip install -r requirements.txt
COPY --chown=app:app . .

VOLUME /data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=6s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=5)"

ENTRYPOINT ["tini", "--"]
CMD ["python", "app.py"]
