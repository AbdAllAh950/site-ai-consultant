FROM python:3.12-slim
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY widget ./widget
COPY demo ./demo
COPY sites ./sites
ENV SITES_DIR=/srv/sites DB_PATH=/data/consultant.sqlite
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
