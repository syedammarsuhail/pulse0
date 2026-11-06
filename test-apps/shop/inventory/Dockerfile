FROM public.ecr.aws/docker/library/golang:latest AS build
WORKDIR /src
COPY main.go .
RUN go mod init shop-inventory && go mod tidy && CGO_ENABLED=0 go build -o /out/app .

FROM public.ecr.aws/docker/library/alpine:3.20
COPY --from=build /out/app /app
ENV OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
ENTRYPOINT ["/app"]
