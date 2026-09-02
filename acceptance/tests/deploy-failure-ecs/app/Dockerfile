FROM public.ecr.aws/docker/library/nginx:1.27-alpine

RUN printf 'server { listen 80; location / { return 500; } }\n' > /etc/nginx/conf.d/default.conf