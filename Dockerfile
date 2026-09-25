# The Sbarbase control plane: supervisor, provisioning worker, console and gateway.
# It drives the pinned upstream containers through the host's Docker daemon, so
# the host needs only Docker. Ubuntu 26.04 ships the /usr/bin/python3 3.14 the
# runtime is tested with. openssh-client provides the ssh-keygen that verifies
# signed release tags (lab/release_channel.py); tzdata lets TZ name a zone such as
# Asia/Dubai for the maintenance window of automatic updates. See docs/guides/docker.md.
FROM ubuntu:26.04
COPY --from=oven/bun:1.3.14 /usr/local/bin/bun /usr/local/bin/bun
COPY --from=docker:29-cli /usr/local/bin/docker /usr/local/bin/docker
RUN apt-get update -q \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y -q --no-install-recommends \
      python3 python3-cryptography git openssh-client tzdata procps ca-certificates util-linux \
 && rm -rf /var/lib/apt/lists/* \
 && git config --system --add safe.directory '*'
COPY deploy/container/start.sh /usr/local/bin/sbarbase-start
ENTRYPOINT ["/usr/local/bin/sbarbase-start"]
