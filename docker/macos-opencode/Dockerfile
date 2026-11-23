# Reviewed Linux OpenCode runtime; the installer verifies the exact release SHA256.
FROM python:3.11-slim
COPY scripts/install_opencode_runtime.py /tmp/install_opencode_runtime.py
RUN python /tmp/install_opencode_runtime.py --destination /usr/local/bin/opencode \
    && rm /tmp/install_opencode_runtime.py \
    && mkdir -p /home/hunter /workspace \
    && useradd --system --uid 10001 --home-dir /home/hunter --shell /usr/sbin/nologin hunter
ENV HOME=/home/hunter
WORKDIR /workspace
USER 10001
