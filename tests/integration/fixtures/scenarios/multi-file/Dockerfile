FROM python:3.12-slim

RUN pip install --no-cache-dir numpy pandas matplotlib
RUN mkdir -p /output

COPY analysis.py /app/analysis.py

CMD ["python3", "/app/analysis.py"]
