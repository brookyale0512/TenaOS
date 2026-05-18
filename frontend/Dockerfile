# Build context is the workspace root so the build stage can pick up both
# `frontend/` source AND the backend metadata contract that the runtime
# config imports for UUID parity.
FROM node:24-bookworm-slim AS build
WORKDIR /workspace

COPY frontend/package.json frontend/package-lock.json frontend/
RUN cd frontend && npm ci

COPY frontend/ frontend/
COPY backend/metadata/required-openmrs-metadata.json backend/metadata/required-openmrs-metadata.json

WORKDIR /workspace/frontend
RUN npm run build

FROM nginx:1.29-alpine

# Default upstream points at the in-network OpenMRS service. Override at
# deploy time when fronting a remote OpenMRS instance.
ENV OPENMRS_UPSTREAM_URL=http://openmrs:8080/openmrs/
ENV CDS_UPSTREAM_URL=http://cds:8095

# IMPORTANT: This image NEVER bakes OpenMRS credentials. Authentication is
# the responsibility of the user-facing /login page, which sets a
# JSESSIONID cookie that nginx forwards to the upstream.

COPY frontend/nginx.conf /etc/nginx/templates/default.conf.template
COPY frontend/security-headers.conf /etc/nginx/conf.d/security-headers.conf
COPY --from=build /workspace/frontend/dist/ /usr/share/nginx/html/
EXPOSE 8080
