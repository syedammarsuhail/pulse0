// No OpenTelemetry packages or code. Instrumented by OpenTelemetry .NET Automatic Instrumentation (CLR profiler).
var builder = WebApplication.CreateBuilder(args);
builder.Services.AddHttpClient();
var app = builder.Build();
var backend = Environment.GetEnvironmentVariable("BACKEND_URL") ?? "http://lang-python:8000";
app.MapGet("/health", () => "ok");
app.MapGet("/work", async (IHttpClientFactory f, ILogger<Program> log) =>
{
    log.LogInformation("handling /work");
    var r = await f.CreateClient().GetAsync(backend + "/data");
    if (!r.IsSuccessStatusCode) { log.LogError("backend returned {Status}", (int)r.StatusCode); return Results.StatusCode(500); }
    return Results.Content(await r.Content.ReadAsStringAsync(), "application/json");
});
app.Run("http://0.0.0.0:8000");
