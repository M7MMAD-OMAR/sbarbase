# Read-only daemon-profile probe layered on the exact source verification image.
# Supply the locally built exact source image; this sentinel is not published.
ARG SOURCE_VERIFY_IMAGE=sbarbase-source-verify:required-source-image
FROM docker:29-cli@sha256:b1805116a6a86cc591b5d5f60a910a0715cdcc9d18d866ad68b1457ead25c35c AS cli
FROM ${SOURCE_VERIFY_IMAGE}
USER root
COPY --from=cli /usr/local/bin/docker /usr/local/bin/docker
ENTRYPOINT ["/usr/bin/python3", "/opt/sbarbase/lab/docker_profile.py", "check"]
