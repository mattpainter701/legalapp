FROM node:20-alpine@sha256:fb4cd12c85ee03686f6af5362a0b0d56d50c58a04632e6c0fb8363f609372293 AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
ARG VITE_API_URL=/api
ARG VITE_PUBLIC_SITE_URL=
ARG VITE_CONTACT_URL=
ARG VITE_PUBLIC_SIGNUP_ENABLED=false
ENV VITE_API_URL=$VITE_API_URL
ENV VITE_PUBLIC_SITE_URL=$VITE_PUBLIC_SITE_URL
ENV VITE_CONTACT_URL=$VITE_CONTACT_URL
ENV VITE_PUBLIC_SIGNUP_ENABLED=$VITE_PUBLIC_SIGNUP_ENABLED
RUN npm run build

FROM node:20-alpine@sha256:fb4cd12c85ee03686f6af5362a0b0d56d50c58a04632e6c0fb8363f609372293
WORKDIR /app
RUN apk add --no-cache curl && npm install -g serve@14.2.6
COPY --from=build /app/dist ./dist
COPY serve.json ./serve.json
EXPOSE 3000
CMD ["serve", "--no-port-switching", "-s", "dist", "-l", "3000", "--config", "/app/serve.json"]
