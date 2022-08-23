FROM python:slim

WORKDIR /app
COPY requirements.txt requirements.txt
RUN pip3 install -r requirements
COPY . .
CMD ["python3", "server.py"]