ARG OLCRTC_REF=master

FROM node:24-alpine AS frontend
WORKDIR /src/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM golang:1.26-alpine3.22 AS olcrtc
ARG OLCRTC_REF
RUN apk add --no-cache git
WORKDIR /src
RUN git clone --depth 1 --branch "${OLCRTC_REF}" https://github.com/openlibrecommunity/olcrtc.git .
RUN go mod download
RUN go build -trimpath -ldflags="-s -w" -o /out/olcrtc ./cmd/olcrtc
RUN mkdir -p /out/data && cp -R data/* /out/data/

FROM python:3.12-alpine
ENV PYTHONUNBUFFERED=1 \
    PANEL_DATA_DIR=/data \
    PANEL_STATIC_DIR=/app/static \
    OLCRTC_BIN=/usr/local/bin/olcrtc \
    OLCRTC_DATA_DIR=/opt/olcrtc-data
WORKDIR /app
COPY backend/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY backend/olcrtc_panel /app/olcrtc_panel
COPY --from=frontend /src/frontend/dist /app/static
COPY --from=olcrtc /out/olcrtc /usr/local/bin/olcrtc
COPY --from=olcrtc /out/data /opt/olcrtc-data
EXPOSE 8080
CMD ["sh", "-c", "uvicorn olcrtc_panel.main:app --host ${PANEL_BIND:-0.0.0.0} --port ${PANEL_PORT:-8080}"]
