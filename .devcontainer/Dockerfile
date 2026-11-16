FROM ghcr.io/astral-sh/uv:0.13.8@sha256:5cc1805e2426484abca4ad46ea783bab281fdb225fd82d8ce6d66a81254bac85 AS uv

FROM mcr.microsoft.com/devcontainers/python:3.14@sha256:66a39503cd5b53c94a57b9a235c6c9371ccf17bafd9c90369e566f638a05ac89

COPY --from=uv /uv /uvx /usr/local/bin/

# Pre-create the cache mount point so the named volume is owned by vscode.
RUN install -d -o vscode -g vscode /home/vscode/.cache /home/vscode/.cache/uv
