# ── Stage 1: Android SDK + Java + Python ──────────────────────────────────────
FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV ANDROID_HOME=/opt/android-sdk
ENV PATH=$PATH:$ANDROID_HOME/cmdline-tools/latest/bin:$ANDROID_HOME/build-tools/34.0.0:$ANDROID_HOME/platform-tools

# Install system deps
RUN apt-get update && apt-get install -y \
    openjdk-17-jdk-headless \
    python3 python3-pip \
    wget unzip curl \
    && rm -rf /var/lib/apt/lists/*

# Install Android command-line tools
RUN mkdir -p $ANDROID_HOME/cmdline-tools && \
    wget -q https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip \
         -O /tmp/cmdtools.zip && \
    unzip -q /tmp/cmdtools.zip -d /tmp/cmdtools && \
    mv /tmp/cmdtools/cmdline-tools $ANDROID_HOME/cmdline-tools/latest && \
    rm /tmp/cmdtools.zip

# Accept licenses and install SDK components
RUN yes | sdkmanager --licenses > /dev/null 2>&1 && \
    sdkmanager "platforms;android-33" "build-tools;34.0.0"

# ── Stage 2: App ───────────────────────────────────────────────────────────────
WORKDIR /app

COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

COPY server.py .
COPY website-to-apk.html .

RUN mkdir -p builds

EXPOSE 8080

CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "1", "--timeout", "600", "--keep-alive", "5", "server:app"]
