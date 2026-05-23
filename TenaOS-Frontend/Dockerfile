# Build context is the workspace root so the build stage can pick up
# both the frontend source AND the backend metadata contract that the
# runtime config imports for UUID parity.
FROM node:24-bookworm-slim AS build
WORKDIR /workspace

COPY TenaOS-Frontend/package.json TenaOS-Frontend/package-lock.json TenaOS-Frontend/
RUN cd TenaOS-Frontend && npm ci

COPY TenaOS-Frontend/ TenaOS-Frontend/
COPY TenaOS-Backend/metadata/required-openmrs-metadata.json \
     TenaOS-Backend/metadata/required-openmrs-metadata.json

WORKDIR /workspace/TenaOS-Frontend
RUN npm run build

FROM nginx:1.29-alpine

# Default upstream points at the in-network OpenMRS service. Override at
# deploy time when fronting a remote OpenMRS instance.
ENV OPENMRS_UPSTREAM_URL=http://tenaos-backend:8080/openmrs/
ENV TENA_AGENT_UPSTREAM_URL=http://tenaagent:8095

# IMPORTANT: This image NEVER bakes OpenMRS credentials. Authentication is
# the responsibility of the user-facing /login page, which sets a
# JSESSIONID cookie that nginx forwards to the upstream.

COPY TenaOS-Frontend/nginx.conf /etc/nginx/templates/default.conf.template
COPY TenaOS-Frontend/security-headers.conf /etc/nginx/conf.d/security-headers.conf
COPY --from=build /workspace/TenaOS-Frontend/dist/ /usr/share/nginx/html/
EXPOSE 8080
