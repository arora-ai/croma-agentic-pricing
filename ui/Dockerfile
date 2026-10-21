# The chat app as one container: the React front end is built in the first stage, the FastAPI backend serves it and the API.
# Build from the repository root (the agents/ package is needed too):
#   az acr build -r <acr> -t croma-chat-v2:<tag> -f ui/Dockerfile .
# Settings arrive as environment variables (see the table in docs/APIM.md): FOUNDRY_PROJECT_ENDPOINT, HUB_URL, HUB_API_KEY, NAME_PREFIX, GATEWAY_KEY ...
# The container signs in to Foundry with its managed identity (DefaultAzureCredential).

FROM node:20-alpine AS web
WORKDIR /web
COPY ui/frontend/package.json ui/frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY ui/frontend/ ./
# Optional sign-in with Entra ID (MSAL). All three are empty by default, which builds the app without any sign-in code running.
ARG VITE_ENTRA_CLIENT_ID=""
ARG VITE_ENTRA_TENANT_ID=""
ARG VITE_API_SCOPE=""
ENV VITE_ENTRA_CLIENT_ID=$VITE_ENTRA_CLIENT_ID VITE_ENTRA_TENANT_ID=$VITE_ENTRA_TENANT_ID VITE_API_SCOPE=$VITE_API_SCOPE
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
COPY ui/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY agents/ ./agents/
COPY ui/__init__.py ./ui/__init__.py
COPY ui/backend/ ./ui/backend/
COPY --from=web /web/dist ./ui/frontend/dist
RUN useradd --system --create-home --uid 10001 app && mkdir -p /app/ui/backend/data && chown -R app /app/ui/backend/data
USER app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PORT=8080
EXPOSE 8080
# One worker: the conversation history is a SQLite file and each turn runs on a thread of this process.
CMD ["sh", "-c", "exec uvicorn ui.backend.main:app --host 0.0.0.0 --port ${PORT} --workers 1 --timeout-keep-alive 75"]
