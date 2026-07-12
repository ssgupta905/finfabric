# Works on HuggingFace Spaces, Google Cloud Run, Fly.io, or any container host.
FROM python:3.11-slim

WORKDIR /app

# System deps: web3 needs a few C extensions but the slim wheel usually covers it.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# HuggingFace Spaces uses port 7860 by default; others usually inject $PORT.
ENV PORT=7860
EXPOSE 7860

CMD ["sh", "-c", "uvicorn app.server:app --host 0.0.0.0 --port ${PORT:-7860}"]
