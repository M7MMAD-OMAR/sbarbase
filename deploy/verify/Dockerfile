FROM oven/bun:1.3.14@sha256:e10577f0db68676a7024391c6e5cb4b879ebd17188ab750cf10024a6d700e5c4 AS bun
FROM ubuntu:26.04@sha256:3595d7fc4286a33fad0fd853a4063e654287a9c3787437d7937c94ca3f7a804e
COPY --from=bun /usr/local/bin/bun /usr/local/bin/bun
# Bootstrap HTTPS trust from the same public snapshot without mutable packages.
ADD --checksum=sha256:f7025ab9b24cd73215510931037b02d6960d89584d0d00afba81851abdbe6ef1 https://snapshot.ubuntu.com/ubuntu/20261002T000000Z/pool/main/c/ca-certificates/ca-certificates_20260223_all.deb /tmp/ca-certificates.deb
RUN dpkg-deb --extract /tmp/ca-certificates.deb /tmp/ca-bootstrap \
 && mkdir -p /etc/ssl/certs \
 && cat /tmp/ca-bootstrap/usr/share/ca-certificates/mozilla/*.crt > /etc/ssl/certs/ca-certificates.crt \
 && printf 'APT::Snapshot "20261002T000000Z";\nAcquire::https::CaInfo "/etc/ssl/certs/ca-certificates.crt";\n' > /etc/apt/apt.conf.d/50snapshot \
 && apt-get update -q -o APT::Update::Error-Mode=any \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y -q --no-install-recommends \
      python3 python3-cryptography git openssh-client systemd procps \
      ca-certificates util-linux tzdata \
 && rm -rf /var/lib/apt/lists/* \
 && rm -rf /tmp/ca-certificates.deb /tmp/ca-bootstrap \
 && mkdir -p /usr/local/share/sbarbase-source-verification \
 && printf 'public-source-verification-v1\n' > /usr/local/share/sbarbase-source-verification/public-tree \
 && chmod 0444 /usr/local/share/sbarbase-source-verification/public-tree \
 && useradd --create-home --uid 10001 sbarbase \
 && mkdir -p /opt/sbarbase /evidence \
 && chown sbarbase:sbarbase /opt/sbarbase /evidence
WORKDIR /opt/sbarbase
COPY --chown=sbarbase:sbarbase package.json bun.lock ./
USER sbarbase
RUN bun install --frozen-lockfile
COPY --chown=sbarbase:sbarbase . .
ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["/usr/bin/python3", "deploy/verify/run_checks.py"]
