FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.lock pyproject.toml ./
RUN pip install --no-cache-dir -r requirements.lock setuptools==80.9.0
COPY kotel_reader ./kotel_reader
COPY migrations ./migrations
RUN pip install --no-deps --no-build-isolation . && useradd --uid 10001 --create-home collector && mkdir /data && chown 10001:10001 /data
USER 10001:10001
VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 CMD ["kotel-reader", "healthcheck"]
ENTRYPOINT ["kotel-reader"]
CMD ["run"]

