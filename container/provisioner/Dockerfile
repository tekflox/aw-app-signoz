# Standalone provisioner sidecar — logs into SigNoz with the managed root
# account, mints a service-account API key, and writes it back into this
# app's own workspace config. Python stdlib only (urllib, json) so the
# retry/idempotency logic in provision.py stays unit-testable with no
# vendored HTTP client — see tests/test_provisioner.py.
FROM python:3.12-alpine

COPY provision.py /app/provision.py
WORKDIR /app

ENTRYPOINT ["python3", "/app/provision.py"]
