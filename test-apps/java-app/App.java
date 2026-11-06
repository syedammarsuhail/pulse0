// Plain Java HTTP server with ZERO OpenTelemetry imports.
// Instrumentation attaches via -javaagent:opentelemetry-javaagent.jar at startup.
import com.sun.net.httpserver.HttpServer;
import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.URI;
import java.net.URLConnection;
import java.util.Random;
import java.util.logging.Logger;

public class App {
    private static final Logger log = Logger.getLogger("java-app");
    private static final Random rnd = new Random();

    public static void main(String[] args) throws IOException {
        HttpServer server = HttpServer.create(new InetSocketAddress("127.0.0.1", 7003), 0);
        server.createContext("/", exchange -> write(exchange, 200, "{\"service\":\"java-app\",\"version\":\"1.0.0\"}"));
        server.createContext("/invoices", exchange -> {
            log.info("generating invoice");
            try { Thread.sleep(15 + rnd.nextInt(40)); } catch (InterruptedException ignored) {}
            write(exchange, 200, "{\"invoice_id\":" + rnd.nextInt(9999) + "}");
        });
        server.createContext("/charge", exchange -> {
            log.info("charging customer");
            try {
                URLConnection c = URI.create("http://127.0.0.1:7001/users").toURL().openConnection();
                c.setConnectTimeout(2000); c.setReadTimeout(2000); c.getInputStream().close();
            } catch (Exception e) { log.warning("upstream failed: " + e.getMessage()); }
            if (rnd.nextDouble() < 0.12) {
                log.severe("stripe api unavailable");
                write(exchange, 503, "{\"error\":\"stripe api unavailable\"}");
            } else {
                write(exchange, 200, "{\"charged\":true,\"amount\":" + (10 + rnd.nextInt(90)) + "}");
            }
        });
        server.start();
        System.out.println("java-app on :7003");
    }

    static void write(com.sun.net.httpserver.HttpExchange ex, int code, String body) throws IOException {
        byte[] b = body.getBytes();
        ex.sendResponseHeaders(code, b.length);
        try (OutputStream os = ex.getResponseBody()) { os.write(b); }
    }
}
