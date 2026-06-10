FROM registry.access.redhat.com/ubi9/go-toolset

# Install system dependencies required by operator tooling
USER 0
RUN dnf install -y \
        git \
        make && \
    # Install GitHub CLI
    dnf install -y 'dnf-command(config-manager)' && \
    dnf config-manager --add-repo https://cli.github.com/packages/rpm/gh-cli.repo && \
    dnf install -y gh && \
    dnf install -y python3.11 && \
    dnf install -y python3.11-pip && \
    dnf clean all

WORKDIR /app

# Install Python dependencies
COPY agent/requirements.txt .
RUN python3.11 -m pip install --no-cache-dir -r requirements.txt

# Copy agent code
COPY agent/ ./

# copy default config, users willing to customize should mount at runtime.
COPY deploy/config /config

# Copy plugins directory (agent/config.py resolves /plugins/oape as absolute path)
COPY plugins /plugins

# Configure git globally while still root, and ensure the home directory is
# writable for arbitrary UIDs (OpenShift runs containers as random UID).
RUN git config --global user.name "openshift-app-platform-shift-bot" && \
    git config --global user.email "267347085+openshift-app-platform-shift-bot@users.noreply.github.com"

RUN chmod -R g=u /opt/app-root/src /config

USER 1001

EXPOSE 8000

CMD gh auth setup && python3.11 main.py
