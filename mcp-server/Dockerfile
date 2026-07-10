FROM python:3.12-slim@sha256:423ed6ab25b1921a477529254bfeeabf5855151dc2c3141699a1bfc852199fbf

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends lbzip2 openssh-client sshpass \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && python -m pip uninstall -y pip

COPY mcp_server ./mcp_server

CMD ["uvicorn", "mcp_server.server:app", "--host", "0.0.0.0", "--port", "8021"]
