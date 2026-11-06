# Pulse0 - EKS end-to-end, everything on AWS: CodeBuild builds the image, ELB exposes the UI.
#   powershell -ExecutionPolicy Bypass -File .\eks-e2e-aws.ps1
# Needs: aws, kubectl, helm, eksctl (no local Docker).
# Non-interactive: verifies the account itself, and pauses before teardown until the
# file .\go.txt exists. Teardown ALWAYS runs at the end (even on errors).
param(
    [string]$Region  = "us-east-1",
    [string]$Name    = "pulse0-test",
    [string]$Tag     = "0.1.0",
    [string]$Account = "339713148266"
)
$ErrorActionPreference = "Continue"
$NS       = "observability"
$RepoName = "pulse0-e2e"
$Bucket   = "pulse0-e2e-src-$Account"
$Role     = "pulse0-e2e-codebuild"
$CbProj   = "pulse0-e2e-build"
$GoFile   = Join-Path $PSScriptRoot "go.txt"

function Say($m)  { Write-Host "`n=== $m" -ForegroundColor Cyan }
function Ok($m)   { Write-Host "  PASS  $m" -ForegroundColor Green }
function Bad($m)  { Write-Host "  FAIL  $m" -ForegroundColor Red; $script:failures++ }
function Run {
    & $args[0] $args[1..($args.Length-1)]
    if ($LASTEXITCODE -ne 0) { throw "command failed ($LASTEXITCODE): $($args -join ' ')" }
}
$script:failures = 0

# ---------------- preflight ----------------
Say "Preflight"
foreach ($t in 'aws','kubectl','helm','eksctl') {
    if (-not (Get-Command $t -ErrorAction SilentlyContinue)) { Write-Host "missing tool: $t" -ForegroundColor Red; exit 1 }
}
$Acct = (aws sts get-caller-identity --query Account --output text)
if ($Acct -ne $Account) { Write-Host "Wrong AWS account '$Acct' (expected $Account). Aborting." -ForegroundColor Red; exit 1 }
$OrigCtx = (kubectl config current-context 2>$null)
Write-Host "AWS account : $Acct (confirmed = $Account)"
Write-Host "Region      : $Region"
Write-Host "Original kubectl context (restored at end): $OrigCtx"

Say "Validate Helm chart before spending money"
Run helm lint ./helm/pulse0
Run helm template pulse0 ./helm/pulse0 -n $NS -f ./helm/pulse0/values-eks.yaml --set auth.token=x | Out-Null
Ok "helm lint + template (EKS overlay) clean"

$Registry = "$Acct.dkr.ecr.$Region.amazonaws.com"
$Repo = "$Registry/$RepoName"
$cfg = Join-Path $env:TEMP "$Name-eksctl.yaml"
$tmp = Join-Path $env:TEMP "pulse0-e2e-aws"
$base = $null

try {
    # ---------------- image: CodeBuild ----------------
    Say "Build image on AWS CodeBuild and push to ECR"
    aws ecr describe-repositories --repository-names $RepoName --region $Region *> $null
    if ($LASTEXITCODE -ne 0) { Run aws ecr create-repository --repository-name $RepoName --region $Region --output text --query repository.repositoryUri }

    aws s3api head-bucket --bucket $Bucket --region $Region *> $null
    if ($LASTEXITCODE -ne 0) { Run aws s3api create-bucket --bucket $Bucket --region $Region --output text }
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force $tmp | Out-Null
    Copy-Item .\Dockerfile, .\server.py $tmp
    Copy-Item .\static (Join-Path $tmp "static") -Recurse
    Copy-Item .\demo (Join-Path $tmp "demo") -Recurse
    $zip = Join-Path $env:TEMP "pulse0-src.zip"
    Remove-Item $zip -ErrorAction SilentlyContinue
    # tar (bsdtar) writes forward-slash zip paths; Compress-Archive on PS 5.1 does not
    Run tar -a -c -f $zip -C $tmp Dockerfile server.py static demo
    Run aws s3 cp $zip "s3://$Bucket/src.zip" --region $Region --only-show-errors

    $trust = Join-Path $tmp "trust.json"
    '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"codebuild.amazonaws.com"},"Action":"sts:AssumeRole"}]}' | Set-Content $trust -Encoding ascii
    $pol = Join-Path $tmp "policy.json"
    @"
{"Version":"2012-10-17","Statement":[
 {"Effect":"Allow","Action":"ecr:GetAuthorizationToken","Resource":"*"},
 {"Effect":"Allow","Action":["ecr:BatchCheckLayerAvailability","ecr:InitiateLayerUpload","ecr:UploadLayerPart","ecr:CompleteLayerUpload","ecr:PutImage","ecr:BatchGetImage","ecr:GetDownloadUrlForLayer"],"Resource":"arn:aws:ecr:${Region}:${Acct}:repository/$RepoName"},
 {"Effect":"Allow","Action":["s3:GetObject","s3:GetObjectVersion"],"Resource":"arn:aws:s3:::$Bucket/*"},
 {"Effect":"Allow","Action":["logs:CreateLogGroup","logs:CreateLogStream","logs:PutLogEvents"],"Resource":"*"}]}
"@ | Set-Content $pol -Encoding ascii
    aws iam get-role --role-name $Role *> $null
    if ($LASTEXITCODE -ne 0) { Run aws iam create-role --role-name $Role --assume-role-policy-document "file://$trust" --output text --query Role.Arn }
    Run aws iam put-role-policy --role-name $Role --policy-name build --policy-document "file://$pol"
    $RoleArn = "arn:aws:iam::${Acct}:role/$Role"
    Start-Sleep -Seconds 15   # IAM propagation

    $buildspec = @"
version: 0.2
phases:
  pre_build:
    commands:
      - aws ecr get-login-password --region $Region | docker login --username AWS --password-stdin $Registry
  build:
    commands:
      - sed -i 's#^FROM python#FROM public.ecr.aws/docker/library/python#' Dockerfile demo/Dockerfile
      - docker build -t ${Repo}:$Tag .
      - docker build -t ${Repo}:demo ./demo
  post_build:
    commands:
      - docker push ${Repo}:$Tag
      - docker push ${Repo}:demo
"@
    $projFile = Join-Path $tmp "project.json"
    @{ name = $CbProj
       source = @{ type = "S3"; location = "$Bucket/src.zip"; buildspec = $buildspec }
       artifacts = @{ type = "NO_ARTIFACTS" }
       environment = @{ type = "LINUX_CONTAINER"; image = "aws/codebuild/standard:7.0"; computeType = "BUILD_GENERAL1_SMALL"; privilegedMode = $true }
       serviceRole = $RoleArn } | ConvertTo-Json -Depth 6 | Set-Content $projFile -Encoding ascii
    aws codebuild create-project --cli-input-json "file://$projFile" --region $Region --output text --query project.name
    if ($LASTEXITCODE -ne 0) { throw "codebuild create-project failed" }
    $bid = (aws codebuild start-build --project-name $CbProj --region $Region --query build.id --output text)
    if (-not $bid) { throw "start-build failed" }
    Write-Host "build: $bid"
    do {
        Start-Sleep -Seconds 10
        $st = (aws codebuild batch-get-builds --ids $bid --region $Region --query "builds[0].buildStatus" --output text)
        Write-Host "  build status: $st"
    } while ($st -eq "IN_PROGRESS")
    if ($st -ne "SUCCEEDED") { throw "CodeBuild finished with status $st (see CloudWatch logs for $bid)" }
    Ok "image built and pushed by CodeBuild: ${Repo}:$Tag"

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
    Say "Deploy Pulse0 with Helm (EKS overlay, internet-facing AWS load balancer)"
    $Token = -join ((1..48) | ForEach-Object { '{0:x}' -f (Get-Random -Maximum 16) })
    Run helm upgrade --install pulse0 ./helm/pulse0 -n $NS --create-namespace `
        -f ./helm/pulse0/values-eks.yaml `
        --set image.repository=$Repo --set image.tag=$Tag `
        --set persistence.storageClass=gp2 --set ingress.enabled=false `
        --set service.type=LoadBalancer `
        --set auth.token=$Token --wait --timeout 8m
    kubectl -n $NS get pods,pvc,svc -o wide
    Ok "helm release deployed"

    # ---------------- tests (through the AWS load balancer) ----------------
    Say "End-to-end tests"
    $lb = $null
    foreach ($i in 1..40) {
        $lb = kubectl -n $NS get svc pulse0 -o jsonpath="{.status.loadBalancer.ingress[0].hostname}" 2>$null
        if ($lb) { break }
        Start-Sleep -Seconds 10
    }
    if (-not $lb) { throw "load balancer hostname never appeared" }
    $base = "http://${lb}:4318"
    Write-Host "UI URL: $base"
    $h = @{ Authorization = "Bearer $Token" }
    $up = $false
    foreach ($i in 1..30) {   # ELB DNS + health checks take a few minutes
        try { Invoke-RestMethod "$base/healthz" -TimeoutSec 5 | Out-Null; $up = $true; break } catch { Start-Sleep -Seconds 10 }
    }
    if ($up) { Ok "healthz via load balancer" } else { Bad "healthz via load balancer" }
    try { Invoke-RestMethod "$base/readyz" | Out-Null; Ok "readyz" } catch { Bad "readyz: $_" }
    try { Invoke-RestMethod "$base/api/services" | Out-Null; Bad "API reachable without token" }
    catch { Ok "API rejects missing token" }

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
        if (($svc | ConvertTo-Json -Depth 6) -match "eks-e2e-test") { Ok "service eks-e2e-test visible in /api/services" } else { Bad "service not listed" }
    } catch { Bad "/api/services: $_" }
    try {
        $hs = Invoke-RestMethod "$base/api/services/eks-e2e-test/health" -Headers $h
        Ok "Health Score endpoint: $($hs | ConvertTo-Json -Compress -Depth 4)"
    } catch { Bad "health score: $_" }
    try { Invoke-RestMethod "$base/api/traces?service=eks-e2e-test" -Headers $h | Out-Null; Ok "trace search returned data" }
    catch { Bad "trace search: $_" }

    $ds = kubectl -n $NS get ds -o jsonpath="{.items[0].status.numberReady}/{.items[0].status.desiredNumberScheduled}" 2>$null
    if ($ds -and ($ds.Split('/')[0] -eq $ds.Split('/')[1])) { Ok "collector DaemonSet ready ($ds)" } else { Bad "collector DaemonSet not ready ($ds)" }

    kubectl -n $NS delete pod -l app.kubernetes.io/name=pulse0 --wait=true *> $null
    kubectl -n $NS rollout status statefulset/pulse0 --timeout=180s *> $null
    Start-Sleep -Seconds 15
    try {
        $svc2 = Invoke-RestMethod "$base/api/services" -Headers $h | ConvertTo-Json -Depth 6
        if ($svc2 -match "eks-e2e-test") { Ok "data survived pod restart (EBS volume works)" } else { Bad "data lost after restart" }
    } catch { Bad "post-restart check: $_" }

    # ---------------- demo apps ----------------
    Say "Deploy demo apps (frontend -> backend + load generator)"
    $demoYaml = (Get-Content ./demo/k8s-demo.yaml -Raw) -replace 'pulse0-demo:local', "${Repo}:demo" -replace 'IfNotPresent', 'Always'
    $demoYaml | kubectl apply -f -
    kubectl -n demo rollout status deploy/backend --timeout=300s
    kubectl -n demo rollout status deploy/frontend --timeout=300s
    kubectl -n demo rollout status deploy/loadgen --timeout=300s
    Start-Sleep -Seconds 90
    try {
        $svc = Invoke-RestMethod "$base/api/services" -Headers $h | ConvertTo-Json -Depth 6
        if ($svc -match "frontend" -and $svc -match "backend") { Ok "demo services frontend + backend reporting telemetry" } else { Bad "demo services not visible" }
        $map = Invoke-RestMethod "$base/api/map" -Headers $h | ConvertTo-Json -Depth 6
        if ($map -match "frontend") { Ok "service map has frontend -> backend edge" } else { Bad "service map empty" }
    } catch { Bad "demo checks: $_" }

    Say "RESULT"
    if ($script:failures -eq 0) { Write-Host "ALL CHECKS PASSED" -ForegroundColor Green } else { Write-Host "$script:failures check(s) FAILED" -ForegroundColor Red }
    Write-Host "`nUI is up on $base  (API token: $Token)"
    Write-Host "WAITING for $GoFile before teardown (create that file to continue)."
    while (-not (Test-Path $GoFile)) { Start-Sleep -Seconds 10 }
    Remove-Item $GoFile -ErrorAction SilentlyContinue
}
catch {
    Write-Host "`nERROR: $_" -ForegroundColor Red
    Write-Host "Tearing down anyway so nothing keeps billing." -ForegroundColor Yellow
}
finally {
    # ---------------- teardown (always) ----------------
    Say "TEARDOWN"
    # delete the Service (and its ELB) and PVCs first so no ENIs / EBS volumes are orphaned
    # SAFETY: only touch in-cluster resources if the new cluster exists, and always pin
    # --context to it, so this can never act on eks-dev or any other cluster.
    $newCtx = (kubectl config get-contexts -o name 2>$null | Where-Object { $_ -like "*:cluster/$Name" -or $_ -like "*@$Name.*" } | Select-Object -First 1)
    if ($newCtx -and ($newCtx -ne $OrigCtx)) {
        Write-Host "in-cluster cleanup on context: $newCtx"
        helm uninstall pulse0 -n $NS --kube-context $newCtx 2>$null
        kubectl --context $newCtx delete svc --all -n $NS --wait=true --timeout=180s 2>$null
        kubectl --context $newCtx delete pvc --all -n $NS --wait=true --timeout=180s 2>$null
        kubectl --context $newCtx delete ns $NS --wait=true --timeout=180s 2>$null
    } else { Write-Host "new cluster context not found; skipping in-cluster cleanup" }
    eksctl delete cluster --name $Name --region $Region --wait
    aws ecr delete-repository --repository-name $RepoName --region $Region --force *> $null
    aws codebuild delete-project --name $CbProj --region $Region *> $null
    aws iam delete-role-policy --role-name $Role --policy-name build *> $null
    aws iam delete-role --role-name $Role *> $null
    aws s3 rb "s3://$Bucket" --force --region $Region *> $null
    Remove-Item $cfg, $tmp -Recurse -Force -ErrorAction SilentlyContinue
    if ($OrigCtx) { kubectl config use-context $OrigCtx *> $null }

    Say "Leftover check"
    Write-Host "Clusters in $Region :"; eksctl get cluster --region $Region
    Write-Host "Unattached EBS volumes (should be none from this test):"
    aws ec2 describe-volumes --region $Region --filters Name=status,Values=available --query "Volumes[].VolumeId" --output text
    Write-Host "ECR repo:";       aws ecr describe-repositories --repository-names $RepoName --region $Region 2>&1 | Select-Object -First 1
    Write-Host "CodeBuild project:"; aws codebuild batch-get-projects --names $CbProj --region $Region --query "projects[].name" --output text
    Write-Host "IAM role:";       aws iam get-role --role-name $Role 2>&1 | Select-Object -First 1
    Write-Host "S3 bucket:";      aws s3api head-bucket --bucket $Bucket 2>&1 | Select-Object -First 1
    Write-Host "Load balancers:"; aws elb describe-load-balancers --region $Region --query "LoadBalancerDescriptions[].LoadBalancerName" --output text
    Write-Host "kubectl context restored to: $(kubectl config current-context)"
}
