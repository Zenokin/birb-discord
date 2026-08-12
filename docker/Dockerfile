FROM python:3.14-alpine

ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies first so this layer stays cached across code-only changes.
COPY requirements.txt /app
RUN pip install --no-cache-dir -r requirements.txt

# Copy code
COPY bot.py /app

# Create data directory
# Data (config, reminders, log) is written to working directory at runtime
# Alpine's busybox has no useradd/groupadd (those are shadow-utils); use
# addgroup/adduser instead.
RUN mkdir -p /app/data \
    && addgroup -g 1000 -S birb \
    && adduser -u 1000 -S -D -H -h /app -G birb birb \
    && chown -R birb:birb /app
USER birb
WORKDIR /app/data
VOLUME ["/app/data"]

CMD ["python", "/app/bot.py"]