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

FROM node:20-alpine@sha256:fb4cd12c85ee03686f6af5362a0b0d56d50c58a04632e6c0fb8363f609372293
WORKDIR /app
RUN apk add --no-cache curl && npm install -g serve@14.2.6
COPY --from=build /app/office-addin/dist ./dist
EXPOSE 3001
CMD ["serve", "--no-port-switching", "-s", "dist", "-l", "3001"]
