FROM python:3.14-slim

WORKDIR /app

COPY bot/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY . /app

CMD ["python", "-m", "bot.main"]
