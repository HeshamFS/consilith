FROM python:3.12-slim@sha256:2259debdc6af85626058a15a7bf44ad6a68b8c98c9ae82a02cf5cdb92f096d47
WORKDIR /app
COPY pyproject.toml requirements.lock README.md LICENSE /app/
RUN pip install --no-cache-dir -r requirements.lock
COPY src /app/src
RUN pip install --no-cache-dir --no-deps . && useradd -u 10001 -m scientist
USER 10001
EXPOSE 8080
CMD ["uvicorn", "consilith_crystallography.server:app", "--factory", "--host", "0.0.0.0", "--port", "8080"]
