FROM nginx:alpine
RUN apk add --no-cache certbot certbot-nginx openssl
COPY nginx.conf /etc/nginx/nginx.conf
EXPOSE 80 443
