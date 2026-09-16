# System Monitoring Dashboard
# Build: docker build -t system-monitor .
# Run (butuh akses /proc + docker socket dari host):
#   docker run -d --name system-monitor -p 9090:9090 \
#     -v /proc:/proc:ro -v /home:/home:ro \
#     -v /var/run/docker.sock:/var/run/docker.sock \
#     -e PROJECTS_ROOT=/home \
#     system-monitor
FROM python:3.12-slim

# procps (ps) untuk top processes akurat + who untuk user count
RUN apt-get update && apt-get install -y --no-install-recommends procps \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY app.py start.sh ./
COPY static/ ./static/

EXPOSE 9090
ENV PORT=9090

CMD ["python3", "app.py", "--host", "0.0.0.0", "--port", "9090"]
