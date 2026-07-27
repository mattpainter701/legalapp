FROM node:20-alpine@sha256:fb4cd12c85ee03686f6af5362a0b0d56d50c58a04632e6c0fb8363f609372293 AS build
WORKDIR /app/office-addin
COPY office-addin/package*.json ./
RUN npm ci
COPY office-addin/ ./
COPY frontend/public/icons/ /app/frontend/public/icons/
ARG OFFICE_ADDIN_ORIGIN
ARG VITE_API_BASE=/api
ARG VITE_OFFICE_ENTRA_CLIENT_ID
ARG VITE_OFFICE_ENTRA_AUTHORITY=https://login.microsoftonline.com/common
ARG VITE_OFFICE_API_SCOPE
ENV OFFICE_ADDIN_ORIGIN=$OFFICE_ADDIN_ORIGIN
ENV VITE_API_BASE=$VITE_API_BASE
ENV VITE_OFFICE_ENTRA_CLIENT_ID=$VITE_OFFICE_ENTRA_CLIENT_ID
ENV VITE_OFFICE_ENTRA_AUTHORITY=$VITE_OFFICE_ENTRA_AUTHORITY
ENV VITE_OFFICE_API_SCOPE=$VITE_OFFICE_API_SCOPE
RUN npm run build

FROM nginx:alpine@sha256:54f2a904c251d5a34adf545a72d32515a15e08418dae0266e23be2e18c66fefa
COPY office-addin/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/office-addin/dist /usr/share/nginx/html
EXPOSE 3001
HEALTHCHECK --interval=5s --timeout=3s --retries=5 CMD wget -qO- http://127.0.0.1:3001/ >/dev/null || exit 1
