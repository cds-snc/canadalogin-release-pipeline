FROM public.ecr.aws/docker/library/nginx:1.27-alpine

ARG APP_VERSION=unknown
RUN printf 'release-pipeline acceptance failure mode\nversion=%s\n' "$APP_VERSION" > /usr/share/nginx/html/index.html
