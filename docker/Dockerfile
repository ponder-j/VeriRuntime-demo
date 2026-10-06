FROM python:3.11-slim@sha256:9534e5a8e315485d4061ed659af0fd78a284c015f9b73661b41d6bab25604534 AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 VRUN_BACKEND=docker VRUN_DOCKER_VOLUME=veriruntime-data VRUN_DOCKER_DATA_ROOT=/data
WORKDIR /app
COPY docker/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY pyproject.toml README.md ./
COPY veriruntime ./veriruntime
RUN pip install --no-cache-dir --no-deps .
COPY examples ./examples
COPY tests ./tests
COPY scripts ./scripts
ENTRYPOINT ["vrun"]
CMD ["doctor"]

FROM python:3.11-slim@sha256:9534e5a8e315485d4061ed659af0fd78a284c015f9b73661b41d6bab25604534 AS worker-base
ENV DEBIAN_FRONTEND=noninteractive PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 LC_ALL=C
RUN apt-get update && apt-get install -y --no-install-recommends gcc libc6-dev libstdc++6 && rm -rf /var/lib/apt/lists/*
WORKDIR /opt/runtime
ENV PYTHONPATH=/opt/runtime
ENTRYPOINT ["python3", "-m", "veriruntime.tools.container_worker"]

FROM worker-base AS fetch
COPY docker/install_verifier.py /install.py
COPY docker/verifiers-linux.lock.json /lock.json
COPY docker/downloads/ /downloads/

FROM fetch AS cbmc-fetch
RUN python3 /install.py cbmc
FROM worker-base AS cbmc
COPY --from=cbmc-fetch /opt/cbmc /opt/cbmc
COPY veriruntime ./veriruntime
ENV VRUN_CBMC=/opt/cbmc/cbmc
CMD ["probe", "cbmc"]

FROM fetch AS esbmc-fetch
RUN python3 /install.py esbmc
FROM worker-base AS esbmc
COPY --from=esbmc-fetch /opt/esbmc /opt/esbmc
COPY veriruntime ./veriruntime
ENV VRUN_ESBMC=/opt/esbmc/release/bin/esbmc
CMD ["probe", "esbmc"]

FROM fetch AS cpa-fetch
RUN python3 /install.py cpachecker
FROM worker-base AS cpachecker
RUN apt-get update && apt-get install -y --no-install-recommends openjdk-21-jre-headless clang && rm -rf /var/lib/apt/lists/*
COPY --from=cpa-fetch /opt/cpachecker /opt/cpachecker
COPY veriruntime ./veriruntime
ENV VRUN_CPACHECKER=/opt/cpachecker/CPAchecker-4.2.2-unix/bin/cpachecker
CMD ["probe", "cpachecker"]
