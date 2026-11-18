FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY taphome_exporter/ ./taphome_exporter/
USER 65534
# ponytail: bytecode writes would fail on the read-only root filesystem, so skip them.
ENV TAPHOME_EXPORTER_PORT=9878 PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
EXPOSE 9878
CMD ["python", "-m", "taphome_exporter.exporter"]
