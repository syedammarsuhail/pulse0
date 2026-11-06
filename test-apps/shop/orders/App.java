// Orders service. No OpenTelemetry imports: attached with -javaagent.
import com.sun.net.httpserver.*;
import java.io.*;
import java.net.InetSocketAddress;
import java.net.URI;
import java.net.http.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicLong;
import java.util.logging.Logger;

public class App {
  static final Logger log = Logger.getLogger("orders");
  static final HttpClient http = HttpClient.newHttpClient();
  static final String PAY = System.getenv().getOrDefault("PAYMENTS_URL", "http://payments:8000");
  static final String INV = System.getenv().getOrDefault("INVENTORY_URL", "http://inventory:8000");
  // simulated message queue: work is handed to a worker pool (the agent propagates context across the executor hop)
  static final ExecutorService queue = Executors.newFixedThreadPool(4);
  static final AtomicLong placed = new AtomicLong(), failed = new AtomicLong();

  static HttpResponse<String> call(String method, String url, String body, String chaos) throws Exception {
    HttpRequest.Builder b = HttpRequest.newBuilder(URI.create(url)).header("content-type", "application/json");
    if (chaos != null && !chaos.isEmpty()) b.header("x-chaos", chaos);
    b.method(method, body == null ? HttpRequest.BodyPublishers.noBody() : HttpRequest.BodyPublishers.ofString(body));
    return http.send(b.build(), HttpResponse.BodyHandlers.ofString());
  }

  static void reply(HttpExchange x, int code, String body) throws IOException {
    byte[] b = body.getBytes();
    x.getResponseHeaders().add("content-type", "application/json");
    x.sendResponseHeaders(code, b.length);
    x.getResponseBody().write(b);
    x.close();
  }

  static boolean chaos(HttpExchange x) throws Exception {
    String c = x.getRequestHeaders().getFirst("x-chaos");
    if (c == null) return false;
    if (c.equals("slow:orders")) { log.warning("injected slowness"); Thread.sleep(1000 + (long) (Math.random() * 800)); }
    if (c.equals("error:orders")) {
      log.severe("injected failure: order store unavailable");
      failed.incrementAndGet();
      reply(x, 500, "{\"error\":\"order store unavailable\"}");
      return true;
    }
    return false;
  }

  public static void main(String[] a) throws Exception {
    HttpServer s = HttpServer.create(new InetSocketAddress(8000), 0);
    s.setExecutor(Executors.newFixedThreadPool(16));
    s.createContext("/health", x -> reply(x, 200, "\"ok\""));
    s.createContext("/stats", x -> {
      try {
        if (chaos(x)) return;
        reply(x, 200, "{\"placed\":" + placed.get() + ",\"failed\":" + failed.get() + "}");
      } catch (Exception e) {
        log.severe("stats failed: " + e);
        try { reply(x, 500, "{}"); } catch (IOException ignored) { }
      }
    });
    s.createContext("/orders", x -> {
      try {
        if (chaos(x)) return;
        String ch = x.getRequestHeaders().getFirst("x-chaos");
        String body = new String(x.getRequestBody().readAllBytes());
        log.info("placing order");
        HttpResponse<String> inv = call("POST", INV + "/reserve", body, ch);
        if (inv.statusCode() >= 500) {
          failed.incrementAndGet();
          log.severe("inventory reserve failed: " + inv.statusCode());
          reply(x, 502, "{\"error\":\"inventory\"}");
          return;
        }
        HttpResponse<String> pay = call("POST", PAY + "/charge", body, ch);
        if (pay.statusCode() >= 500) {
          failed.incrementAndGet();
          log.severe("payment failed: " + pay.statusCode());
          reply(x, 502, "{\"error\":\"payment\"}");
          return;
        }
        long id = placed.incrementAndGet();
        queue.submit(() -> {   // async fulfilment hop
          try {
            Thread.sleep(40);
            HttpResponse<String> r = call("POST", INV + "/ship", "{\"order\":" + id + "}", null);
            log.info("shipment queued, inventory said " + r.statusCode());
          } catch (Exception e) { log.severe("fulfilment failed: " + e); }
        });
        reply(x, 200, "{\"order\":" + id + ",\"status\":\"confirmed\"}");
      } catch (Exception e) {
        failed.incrementAndGet();
        log.severe("order failed: " + e);
        try { reply(x, 500, "{\"error\":\"internal\"}"); } catch (IOException ignored) { }
      }
    });
    s.start();
    log.info("orders listening on 8000");
  }
}
