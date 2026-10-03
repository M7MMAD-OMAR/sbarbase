# The Sbarbase control plane: supervisor, provisioning worker, console and gateway.
# It drives the pinned upstream containers through the host's Docker daemon, so
# the host needs only Docker. Ubuntu 26.04 ships the /usr/bin/python3 3.14 the
# runtime is tested with. openssh-client provides the ssh-keygen that verifies
# signed release tags (lab/release_channel.py); tzdata lets TZ name a zone such as
# Asia/Dubai for the maintenance window of automatic updates. See docs/guides/docker.md.
FROM ubuntu:26.04@sha256:3595d7fc4286a33fad0fd853a4063e654287a9c3787437d7937c94ca3f7a804e
COPY --from=oven/bun:1.3.14@sha256:e10577f0db68676a7024391c6e5cb4b879ebd17188ab750cf10024a6d700e5c4 /usr/local/bin/bun /usr/local/bin/bun
COPY --from=docker:29-cli@sha256:b1805116a6a86cc591b5d5f60a910a0715cdcc9d18d866ad68b1457ead25c35c /usr/local/bin/docker /usr/local/bin/docker
# Freeze package indexes and bootstrap HTTPS trust from that public snapshot.
ADD --checksum=sha256:f7025ab9b24cd73215510931037b02d6960d89584d0d00afba81851abdbe6ef1 https://snapshot.ubuntu.com/ubuntu/20261002T000000Z/pool/main/c/ca-certificates/ca-certificates_20260223_all.deb /tmp/ca-certificates.deb
RUN dpkg-deb --extract /tmp/ca-certificates.deb /tmp/ca-bootstrap \
 && mkdir -p /etc/ssl/certs \
 && cat /tmp/ca-bootstrap/usr/share/ca-certificates/mozilla/*.crt > /etc/ssl/certs/ca-certificates.crt \
 && printf 'APT::Snapshot "20261002T000000Z";\nAcquire::https::CaInfo "/etc/ssl/certs/ca-certificates.crt";\n' > /etc/apt/apt.conf.d/50snapshot \
 && apt-get update -q -o APT::Update::Error-Mode=any \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y -q --no-install-recommends \
      python3 python3-cryptography git openssh-client tzdata procps ca-certificates util-linux \
 && rm -rf /var/lib/apt/lists/* \
 && rm -rf /tmp/ca-certificates.deb /tmp/ca-bootstrap \
 && git config --system --add safe.directory '*'
COPY --chmod=0755 deploy/container/start.sh /usr/local/bin/sbarbase-start
COPY lab/docker_profile.py /usr/local/lib/sbarbase/docker_profile.py
ENTRYPOINT ["/usr/local/bin/sbarbase-start"]
