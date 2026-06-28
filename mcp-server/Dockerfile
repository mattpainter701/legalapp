FROM python:3.12-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends lbzip2 openssh-client sshpass \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY mcp_server ./mcp_server

CMD ["uvicorn", "mcp_server.server:app", "--host", "0.0.0.0", "--port", "8021"]
