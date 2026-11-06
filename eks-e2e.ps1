# Pulse0 - EKS end-to-end: create cluster -> deploy -> test -> TEAR DOWN everything.
# Run from the project root in PowerShell:
#   powershell -ExecutionPolicy Bypass -File .\eks-e2e.ps1
# Needs: aws, kubectl, helm, eksctl, docker (Docker Desktop running).
# Teardown ALWAYS runs at the end (even on errors) so nothing keeps billing you.
param(
    [string]$Region = "us-east-1",
    [string]$Name   = "pulse0-test",
    [string]$Tag    = "0.1.0"
)
$ErrorActionPreference = "Continue"
$NS = "observability"
$RepoName = "pulse0-e2e"

function Say($m)  { Write-Host "`n=== $m" -ForegroundColor Cyan }
function Ok($m)   { Write-Host "  PASS  $m" -ForegroundColor Green }
function Bad($m)  { Write-Host "  FAIL  $m" -ForegroundColor Red; $script:failures++ }
function Run {
    # run a native command, stop the main flow if it fails
    & $args[0] $args[1..($args.Length-1)]
    if ($LASTEXITCODE -ne 0) { throw "command failed ($LASTEXITCODE): $($args -join ' ')" }
}
$script:failures = 0

# ---------------- preflight (no cost yet) ----------------
Say "Preflight"
foreach ($t in 'aws','kubectl','helm','eksctl','docker') {
    if (-not (Get-Command $t -ErrorAction SilentlyContinue)) { Write-Host "missing tool: $t" -ForegroundColor Red; exit 1 }
}
docker info *> $null
if ($LASTEXITCODE -ne 0) { Write-Host "Docker is installed but not running. Start Docker Desktop and retry." -ForegroundColor Red; exit 1 }
$Acct = (aws sts get-caller-identity --query Account --output text)
if (-not $Acct) { Write-Host "AWS credentials not working" -ForegroundColor Red; exit 1 }
$OrigCtx = (kubectl config current-context 2>$null)
Write-Host "AWS account : $Acct"
Write-Host "Region      : $Region"
Write-Host "Cluster     : $Name  (2 x t3.medium, about 0.18 USD/hour)"
Write-Host "Original kubectl context (restored at end): $OrigCtx"
$ok = Read-Host "Create these in account $Acct? Type yes"
if ($ok -ne "yes") { Write-Host "aborted"; exit 1 }

Say "Validate Helm chart before spending money"
Run helm lint ./helm/pulse0
Run helm template pulse0 ./helm/pulse0 -n $NS -f ./helm/pulse0/values-eks.yaml --set auth.token=x | Out-Null
Ok "helm lint + template (EKS overlay) clean"

$Registry = "$Acct.dkr.ecr.$Region.amazonaws.com"
$Repo = "$Registry/$RepoName"
$cfg = Join-Path $env:TEMP "$Name-eksctl.yaml"
$pf = $null

try {
    # ---------------- image ----------------
    Say "Build and push image to ECR"
    aws ecr describe-repositories --repository-names $RepoName --region $Region *> $null
    if ($LASTEXITCODE -ne 0) { Run aws ecr create-repository --repository-name $RepoName --region $Region --output text --query repository.repositoryUri }
    aws ecr get-login-password --region $Region | docker login --username AWS --password-stdin $Registry
    if ($LASTEXITCODE -ne 0) { throw "ecr login failed" }
    Run docker build -t "${Repo}:$Tag" .
    Run docker push "${Repo}:$Tag"
    Ok "image pushed: ${Repo}:$Tag"

    # ---------------- cluster ----------------
    Say "Create EKS cluster (about 15 minutes)"
@"
apiVersion: eksctl.io/v1alpha5
kind: ClusterConfig
metadata:
  name: $Name
  region: $Region
managedNodeGroups:
  - name: ng
    instanceType: t3.medium
    desiredCapacity: 2
    minSize: 2
    maxSize: 2
    volumeSize: 30
    iam:
      withAddonPolicies:
        ebs: true
addons:
  - name: aws-ebs-csi-driver
"@ | Set-Content -Path $cfg -Encoding ascii
    Run eksctl create cluster -f $cfg
    Run kubectl get nodes
    Ok "cluster up"

    # ---------------- deploy ----------------
    Say "Deploy Pulse0 with Helm (EKS overlay)"
    $Token = -join ((1..48) | ForEach-Object { '{0:x}' -f (Get-Random -Maximum 16) })
    Run helm upgrade --install pulse0 ./helm/pulse0 -n $NS --create-namespace `
        -f ./helm/pulse0/values-eks.yaml `
        --set image.repository=$Repo --set image.tag=$Tag `
        --set persistence.storageClass=gp2 --set ingress.enabled=false `
        --set auth.token=$Token --wait --timeout 8m
    kubectl -n $NS get pods,pvc,svc -o wide
    Ok "helm release deployed"

    # ---------------- tests ----------------
    Say "End-to-end tests"
    $pf = Start-Process kubectl -ArgumentList "-n",$NS,"port-forward","svc/pulse0","4318:4318" -WindowStyle Hidden -PassThru
    Start-Sleep -Seconds 6
    $base = "http://localhost:4318"
    $h = @{ Authorization = "Bearer $Token" }

    try { $r = Invoke-RestMethod "$base/healthz"; Ok "healthz" } catch { Bad "healthz: $_" }
    try { $r = Invoke-RestMethod "$base/readyz";  Ok "readyz"  } catch { Bad "readyz: $_" }

    # auth must reject an unauthenticated API call
    try { Invoke-RestMethod "$base/api/services" | Out-Null; Bad "API reachable without token" }
    catch { Ok "API rejects missing token" }

    # send traces: 8 OK + 2 errors
    function Hex($n) { -join ((1..$n) | ForEach-Object { '{0:x}' -f (Get-Random -Maximum 16) }) }
    $now = [int64]([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()) * 1000000
    $spans = @()
    1..10 | ForEach-Object {
        $code = if ($_ -le 2) { 2 } else { 1 }
        $spans += @{ traceId = (Hex 32); spanId = (Hex 16); parentSpanId = ""; name = "GET /orders"; kind = 2
                     startTimeUnixNano = "$($now - 100000000)"; endTimeUnixNano = "$now"
                     status = @{ code = $code }; attributes = @() }
    }
    $body = @{ resourceSpans = @(@{
        resource = @{ attributes = @(@{ key = "service.name"; value = @{ stringValue = "eks-e2e-test" } }) }
        scopeSpans = @(@{ spans = $spans }) }) } | ConvertTo-Json -Depth 12
    try { Invoke-RestMethod "$base/v1/traces" -Method Post -Body $body -ContentType "application/json" -Headers $h | Out-Null; Ok "OTLP traces accepted" }
    catch { Bad "OTLP ingest: $_" }

    Start-Sleep -Seconds 3
    try {
        $svc = Invoke-RestMethod "$base/api/services" -Headers $h
        $names = ($svc | ConvertTo-Json -Depth 6)
        if ($names -match "eks-e2e-test") { Ok "service eks-e2e-test visible in /api/services" } else { Bad "service not listed" }
    } catch { Bad "/api/services: $_" }
    try {
        $hs = Invoke-RestMethod "$base/api/services/eks-e2e-test/health" -Headers $h
        Ok "Health Score endpoint: $($hs | ConvertTo-Json -Compress -Depth 4)"
    } catch { Bad "health score: $_" }
    try {
        $tr = Invoke-RestMethod "$base/api/traces?service=eks-e2e-test" -Headers $h
        Ok "trace search returned data"
    } catch { Bad "trace search: $_" }

    # collector DaemonSet should be Ready on both nodes
    $ds = kubectl -n $NS get ds -o jsonpath="{.items[0].status.numberReady}/{.items[0].status.desiredNumberScheduled}" 2>$null
    if ($ds -and ($ds.Split('/')[0] -eq $ds.Split('/')[1])) { Ok "collector DaemonSet ready ($ds)" } else { Bad "collector DaemonSet not ready ($ds)" }

    # persistence: restart pod, data must survive
    kubectl -n $NS delete pod -l app.kubernetes.io/name=pulse0 --wait=true *> $null
    kubectl -n $NS rollout status statefulset/pulse0 --timeout=180s *> $null
    Start-Sleep -Seconds 4
    if ($pf) { Stop-Process -Id $pf.Id -Force -ErrorAction SilentlyContinue }
    $pf = Start-Process kubectl -ArgumentList "-n",$NS,"port-forward","svc/pulse0","4318:4318" -WindowStyle Hidden -PassThru
    Start-Sleep -Seconds 6
    try {
        $svc2 = Invoke-RestMethod "$base/api/services" -Headers $h | ConvertTo-Json -Depth 6
        if ($svc2 -match "eks-e2e-test") { Ok "data survived pod restart (EBS volume works)" } else { Bad "data lost after restart" }
    } catch { Bad "post-restart check: $_" }

    Say "RESULT"
    if ($script:failures -eq 0) { Write-Host "ALL CHECKS PASSED" -ForegroundColor Green } else { Write-Host "$script:failures check(s) FAILED" -ForegroundColor Red }
    Write-Host "`nUI is open on $base  (API token: $Token)"
    Write-Host "Take a look in your browser. Nothing is torn down until you press Enter."
    Read-Host "Press Enter to DELETE the cluster and everything else"
}
catch {
    Write-Host "`nERROR: $_" -ForegroundColor Red
    Write-Host "Tearing down anyway so nothing keeps billing." -ForegroundColor Yellow
}
finally {
    # ---------------- teardown (always) ----------------
    Say "TEARDOWN"
    if ($pf) { Stop-Process -Id $pf.Id -Force -ErrorAction SilentlyContinue }
    # release EBS volumes first, so the cluster delete leaves no orphans
    helm uninstall pulse0 -n $NS 2>$null
    kubectl delete pvc --all -n $NS --wait=true --timeout=180s 2>$null
    kubectl delete ns $NS --wait=true --timeout=180s 2>$null
    eksctl delete cluster --name $Name --region $Region --wait
    aws ecr delete-repository --repository-name $RepoName --region $Region --force *> $null
    Remove-Item $cfg -ErrorAction SilentlyContinue
    if ($OrigCtx) { kubectl config use-context $OrigCtx *> $null }

    Say "Leftover check"
    Write-Host "Clusters in $Region :"; eksctl get cluster --region $Region
    Write-Host "Unattached EBS volumes (should be none from this test):"
    aws ec2 describe-volumes --region $Region --filters Name=status,Values=available --query "Volumes[].VolumeId" --output text
    Write-Host "kubectl context restored to: $(kubectl config current-context)"
}
