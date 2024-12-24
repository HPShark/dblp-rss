FROM python:slim

WORKDIR /app
COPY requirements.txt requirements.txt
RUN pip3 install -r requirements.txt
COPY . .

EXPOSE 8080
VOLUME [ "/app/cache" ]

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8080"]
