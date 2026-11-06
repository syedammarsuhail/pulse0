// No OpenTelemetry imports. Attached via -javaagent.
import com.sun.net.httpserver.HttpServer;
import java.net.InetSocketAddress;
import java.net.URI;
import java.net.http.*;
import java.util.logging.Logger;

public class App {
  static final Logger log = Logger.getLogger("lang-java");
  public static void main(String[] a) throws Exception {
    String backend = System.getenv().getOrDefault("BACKEND_URL", "http://lang-python:8000");
    HttpClient c = HttpClient.newHttpClient();
    HttpServer s = HttpServer.create(new InetSocketAddress(8000), 0);
    s.createContext("/health", x -> { byte[] b = "ok".getBytes(); x.sendResponseHeaders(200, b.length); x.getResponseBody().write(b); x.close(); });
    s.createContext("/work", x -> {
      log.info("handling /work");
      int code = 200; String body = "{}";
      try {
        HttpResponse<String> r = c.send(HttpRequest.newBuilder(URI.create(backend + "/data")).build(), HttpResponse.BodyHandlers.ofString());
        code = r.statusCode(); body = r.body();
        if (code >= 500) log.severe("backend returned " + code);
      } catch (Exception e) { code = 500; log.severe("work failed: " + e); }
      byte[] b = body.getBytes(); x.sendResponseHeaders(code, b.length); x.getResponseBody().write(b); x.close();
    });
    s.start(); log.info("listening on 8000");
  }
}
