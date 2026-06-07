FROM nginx:alpine
RUN apk add --no-cache certbot certbot-nginx openssl
COPY nginx.conf /etc/nginx/nginx.conf
COPY snippets/ /etc/nginx/snippets/
EXPOSE 80 443
