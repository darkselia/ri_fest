FROM python:3.13.16-slim-bookworm

ENV VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONNOUSERSITE=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONTZPATH=""

COPY requirements.txt /tmp/requirements.txt
RUN python -m venv "$VIRTUAL_ENV" \
    && pip install --no-cache-dir -r /tmp/requirements.txt \
    && pip check

# Keep the existing Beat volume's numeric owner when replacing Micromamba.
RUN groupadd --gid 57439 app \
    && useradd --uid 57439 --gid app --create-home app \
    && mkdir -p /app /var/lib/ri-fest/beat \
    && chown app:app /app /var/lib/ri-fest/beat
WORKDIR /app
COPY --chown=app:app . /app
USER app
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000", "--noreload"]
