FROM python:3.12-slim

WORKDIR /app
COPY relay_server.py /app/relay_server.py

ENV PYTHONUNBUFFERED=1         POLKACREW_HOST=0.0.0.0         POLKACREW_MAX_POSTS_PER_SECOND=120

EXPOSE 10000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3       CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:' + __import__('os').environ.get('PORT','10000') + '/health', timeout=3).read()" || exit 1

CMD ["python3", "/app/relay_server.py"]
