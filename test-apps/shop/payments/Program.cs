// Payments service. No OpenTelemetry packages or code: instrumented by OpenTelemetry .NET Automatic Instrumentation.
var builder = WebApplication.CreateBuilder(args);
var app = builder.Build();
var rnd = new Random();
long charged = 0;

app.MapGet("/health", () => "ok");
app.MapPost("/charge", async (HttpRequest req, ILogger<Program> log) =>
{
    var chaos = req.Headers["x-chaos"].ToString();
    if (chaos == "slow:payments") { log.LogWarning("injected slowness"); await Task.Delay(rnd.Next(1000, 1800)); }
    if (chaos == "error:payments")
    {
        log.LogError("injected failure: card processor timeout");
        return Results.Json(new { error = "card processor timeout" }, statusCode: 500);
    }
    await Task.Delay(rnd.Next(20, 60)); // pretend to call the card network
    var id = Interlocked.Increment(ref charged);
    log.LogInformation("charged order payment {PaymentId}", id);
    return Results.Json(new { payment = id, status = "captured" });
});
app.Run("http://0.0.0.0:8000");
