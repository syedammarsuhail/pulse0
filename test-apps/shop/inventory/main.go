// Inventory service in Go. Go has no true zero-code instrumentation (except eBPF, not used here), so this uses the
// OpenTelemetry SDK + otelhttp, plus a manual span for the (simulated) database and a business metric.
package main

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"math/rand"
	"net/http"
	"sync"
	"time"

	"go.opentelemetry.io/contrib/bridges/otelslog"
	"go.opentelemetry.io/contrib/instrumentation/net/http/otelhttp"
	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/codes"
	"go.opentelemetry.io/otel/exporters/otlp/otlplog/otlploghttp"
	"go.opentelemetry.io/otel/exporters/otlp/otlpmetric/otlpmetrichttp"
	"go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracehttp"
	"go.opentelemetry.io/otel/log/global"
	"go.opentelemetry.io/otel/metric"
	"go.opentelemetry.io/otel/propagation"
	sdklog "go.opentelemetry.io/otel/sdk/log"
	sdkmetric "go.opentelemetry.io/otel/sdk/metric"
	"go.opentelemetry.io/otel/sdk/resource"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
	"go.opentelemetry.io/otel/trace"
)

var (
	mu    sync.Mutex
	stock = map[string]int{"widget": 1000, "gizmo": 1000, "gadget": 1000}
)

func main() {
	ctx := context.Background()
	res, _ := resource.New(ctx, resource.WithFromEnv())
	te, err := otlptracehttp.New(ctx)
	must(err)
	otel.SetTracerProvider(sdktrace.NewTracerProvider(sdktrace.WithBatcher(te), sdktrace.WithResource(res)))
	otel.SetTextMapPropagator(propagation.NewCompositeTextMapPropagator(propagation.TraceContext{}, propagation.Baggage{}))
	me, err := otlpmetrichttp.New(ctx)
	must(err)
	otel.SetMeterProvider(sdkmetric.NewMeterProvider(sdkmetric.WithReader(sdkmetric.NewPeriodicReader(me, sdkmetric.WithInterval(15*time.Second))), sdkmetric.WithResource(res)))
	le, err := otlploghttp.New(ctx)
	must(err)
	global.SetLoggerProvider(sdklog.NewLoggerProvider(sdklog.WithProcessor(sdklog.NewBatchProcessor(le)), sdklog.WithResource(res)))
	log := otelslog.NewLogger("inventory")

	tracer := otel.Tracer("inventory")
	meter := otel.Meter("inventory")
	reserved, _ := meter.Int64Counter("shop.inventory.reserved", metric.WithDescription("units reserved"))
	shipped, _ := meter.Int64Counter("shop.inventory.shipped", metric.WithDescription("shipments dispatched"))

	chaos := func(w http.ResponseWriter, r *http.Request) bool {
		c := r.Header.Get("x-chaos")
		if c == "slow:inventory" {
			log.WarnContext(r.Context(), "injected slowness")
			time.Sleep(time.Duration(1000+rand.Intn(800)) * time.Millisecond)
		}
		if c == "error:inventory" {
			log.ErrorContext(r.Context(), "injected failure: stock database unreachable")
			http.Error(w, `{"error":"stock database unreachable"}`, http.StatusInternalServerError)
			return true
		}
		return false
	}
	db := func(ctx context.Context, op string, d time.Duration) {
		_, span := tracer.Start(ctx, "SELECT stock", trace.WithAttributes(attribute.String("db.system", "postgresql"), attribute.String("db.operation", op)))
		defer span.End()
		time.Sleep(d)
		span.SetStatus(codes.Ok, "")
	}

	mux := http.NewServeMux()
	mux.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) { fmt.Fprint(w, "ok") })
	mux.HandleFunc("/reserve", func(w http.ResponseWriter, r *http.Request) {
		if chaos(w, r) {
			return
		}
		db(r.Context(), "reserve", time.Duration(5+rand.Intn(20))*time.Millisecond)
		mu.Lock()
		stock["widget"]--
		left := stock["widget"]
		mu.Unlock()
		reserved.Add(r.Context(), 1)
		log.InfoContext(r.Context(), "stock reserved", slog.Int("left", left))
		json.NewEncoder(w).Encode(map[string]any{"reserved": true, "left": left})
	})
	mux.HandleFunc("/ship", func(w http.ResponseWriter, r *http.Request) {
		if chaos(w, r) {
			return
		}
		db(r.Context(), "ship", time.Duration(5+rand.Intn(10))*time.Millisecond)
		shipped.Add(r.Context(), 1)
		log.InfoContext(r.Context(), "shipment dispatched")
		json.NewEncoder(w).Encode(map[string]any{"shipped": true})
	})
	log.Info("inventory listening on 8000")
	must(http.ListenAndServe(":8000", otelhttp.NewHandler(mux, "inventory", otelhttp.WithFilter(func(r *http.Request) bool { return r.URL.Path != "/health" }))))
}

func must(err error) {
	if err != nil {
		panic(err)
	}
}
