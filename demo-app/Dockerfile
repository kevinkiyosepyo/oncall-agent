FROM python:3.12-slim

WORKDIR /srv/app

RUN pip install --no-cache-dir "fastapi>=0.115" "uvicorn[standard]>=0.30"

COPY src/ .

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
