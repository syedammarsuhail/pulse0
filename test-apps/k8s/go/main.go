// Go has no true zero-code instrumentation (except eBPF, not attempted here): this uses the OTel SDK + otelhttp.
package main

import (
	"context"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"os"

	"go.opentelemetry.io/contrib/bridges/otelslog"
	"go.opentelemetry.io/contrib/instrumentation/net/http/otelhttp"
	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/exporters/otlp/otlplog/otlploghttp"
	"go.opentelemetry.io/otel/exporters/otlp/otlpmetric/otlpmetrichttp"
	"go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracehttp"
	"go.opentelemetry.io/otel/log/global"
	"go.opentelemetry.io/otel/propagation"
	sdklog "go.opentelemetry.io/otel/sdk/log"
	sdkmetric "go.opentelemetry.io/otel/sdk/metric"
	"go.opentelemetry.io/otel/sdk/resource"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
)

func main() {
	ctx := context.Background()
	res, _ := resource.New(ctx, resource.WithFromEnv())
	te, err := otlptracehttp.New(ctx)
	must(err)
	otel.SetTracerProvider(sdktrace.NewTracerProvider(sdktrace.WithBatcher(te), sdktrace.WithResource(res)))
	otel.SetTextMapPropagator(propagation.TraceContext{})
	me, err := otlpmetrichttp.New(ctx)
	must(err)
	otel.SetMeterProvider(sdkmetric.NewMeterProvider(sdkmetric.WithReader(sdkmetric.NewPeriodicReader(me)), sdkmetric.WithResource(res)))
	le, err := otlploghttp.New(ctx)
	must(err)
	global.SetLoggerProvider(sdklog.NewLoggerProvider(sdklog.WithProcessor(sdklog.NewBatchProcessor(le)), sdklog.WithResource(res)))
	log := otelslog.NewLogger("lang-go")

	backend := os.Getenv("BACKEND_URL")
	if backend == "" {
		backend = "http://lang-python:8000"
	}
	client := &http.Client{Transport: otelhttp.NewTransport(http.DefaultTransport)}
	mux := http.NewServeMux()
	mux.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) { fmt.Fprint(w, "ok") })
	mux.HandleFunc("/work", func(w http.ResponseWriter, r *http.Request) {
		log.InfoContext(r.Context(), "handling /work")
		req, _ := http.NewRequestWithContext(r.Context(), "GET", backend+"/data", nil)
		resp, err := client.Do(req)
		if err != nil || resp.StatusCode >= 500 {
			log.ErrorContext(r.Context(), "backend failed", slog.Any("err", err))
			http.Error(w, "backend failed", 500)
			return
		}
		defer resp.Body.Close()
		b, _ := io.ReadAll(resp.Body)
		w.Write(b)
	})
	log.Info("listening on 8000")
	must(http.ListenAndServe(":8000", otelhttp.NewHandler(mux, "lang-go", otelhttp.WithFilter(func(r *http.Request) bool { return r.URL.Path != "/health" }))))
}

func must(err error) {
	if err != nil {
		panic(err)
	}
}
