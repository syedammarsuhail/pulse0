FROM public.ecr.aws/docker/library/nginx:1.27-alpine
# otel-rum.js is the vendored browser bundle from test-apps/angular-app (copied in by build.sh)
COPY index.html otel-rum.js /usr/share/nginx/html/
COPY default.conf /etc/nginx/conf.d/default.conf
# PULSE0_URL is substituted into index.html at container start
CMD ["/bin/sh", "-c", "sed -i \"s|__PULSE0_URL__|${PULSE0_URL}|\" /usr/share/nginx/html/index.html && exec nginx -g 'daemon off;'"]
