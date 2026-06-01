FROM node:20-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm install
COPY . .
RUN npm run build

FROM node:20-alpine
WORKDIR /app
RUN apk add --no-cache curl && npm install -g serve@latest
COPY --from=build /app/dist ./dist
EXPOSE 3000
CMD ["serve", "--no-port-switching", "-s", "dist", "-l", "3000"]
