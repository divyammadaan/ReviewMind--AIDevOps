# CodiumAI / Qodo "cover-agent" test-generation agent.
#
# WHY A CONTAINER
# cover-agent requires Python >=3.9.17,<3.14. Ubuntu 26.04 ships ONLY Python
# 3.14 and provides no alternative interpreter in apt (no 3.13/3.12, no
# deadsnakes for this release). Rather than compile a second interpreter onto
# the host or add a third-party PPA, we pin the supported interpreter in an
# image. Nothing on the host changes.
#
# SAFETY NOTE
# cover-agent works by GENERATING tests and then RUNNING them to measure
# coverage -- i.e. it executes code. That is acceptable here because it is
# pointed exclusively at OUR OWN evaluation corpus, never at pull-request code.
# ReviewMind's review pipeline remains strictly read-only. The container adds a
# second layer of isolation around that execution.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# git is required because we install cover-agent from source (it is not on PyPI).
RUN apt-get update \
 && apt-get install -y --no-install-recommends git build-essential \
 && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir pytest pytest-cov \
 && pip install --no-cache-dir "git+https://github.com/qodo-ai/qodo-cover.git@0.3.10"

WORKDIR /work
# cover-agent reads the model/key from the environment via litellm.
ENTRYPOINT ["cover-agent"]
