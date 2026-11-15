---
description: Build, deploy, DNS-map, smoke-test, or tear down Pulse0 on a temporary AWS EKS cluster
argument-hint: "[all|deploy|test|dns|teardown]  (default: all)"
allowed-tools: Bash(bash scripts/eks.sh:*), Bash(git rev-parse:*), Bash(aws sts get-caller-identity:*)
---

Run the Pulse0 EKS pipeline wrapper `scripts/eks.sh`. The requested action is **$1** (if empty, use `all`).

This provisions **real, billable AWS infrastructure** (an EKS cluster with 2× t3.medium nodes, an ELB, ECR, CodeBuild, S3). Treat `deploy`/`all` as a paid, create-infrastructure action.

## What each action does
- `all` (default) — build image in CodeBuild → create the EKS cluster if missing → Helm deploy Pulse0 + demo apps → map DNS (if configured) → smoke-test. ~20 min on a cold cluster.
- `deploy` — same as `all` but without the smoke test.
- `test` — run `scripts/smoke.py` against the already-deployed load balancer.
- `dns` — (re)point the Route53 hostname at the current load balancer. Requires `PULSE0_DNS_NAME` + `PULSE0_HOSTED_ZONE_ID`.
- `teardown` — delete the cluster, ELB, ECR repo, CodeBuild project, S3 bucket, IAM role, and the Route53 record (the database is **never** touched).

## Required before deploy
- Tools on PATH: `aws`, `eksctl`, `kubectl`, `helm`, `python` (all confirmed present on this machine).
- Env `SUPABASE_DB_URL` — the Postgres connection string (stored only in a Kubernetes secret, never printed).
- Optional for a friendly hostname instead of the raw `*.elb.amazonaws.com` name:
  - `PULSE0_DNS_NAME` — e.g. `pulse0.example.com`
  - `PULSE0_HOSTED_ZONE_ID` — the Route53 hosted zone that owns that domain
- Optional **in-VPC RDS Postgres backend** (low-latency, private, created inside the cluster's own VPC and torn down with it):
  - `RDS_IN_VPC=1` — provision RDS and wire Pulse0 to it (instead of SQLite-on-EBS)
  - `RDS_CLASS` — instance class (default `db.t4g.small`)
  - Without `RDS_IN_VPC`, and with no `SUPABASE_DB_URL`, the deploy uses **SQLite-on-EBS** (free, fast, single-pod).
- Optional overrides: `AWS_REGION` (default `us-east-1`), `CLUSTER_NAME` (default `pulse0-test`), `PULSE0_TOKEN`, `RETENTION_HOURS` (default 6).

## How to run it
1. Confirm the target AWS account first: `aws sts get-caller-identity` — show it to the user.
2. For `deploy`/`all`/`dns`, verify `SUPABASE_DB_URL` is set; for `dns`, also `PULSE0_DNS_NAME` + `PULSE0_HOSTED_ZONE_ID`. If a required var is missing, stop and ask the user to export it (do not invent a value).
3. Because `deploy`/`all`/`teardown` spend or destroy billable resources, **state the action, the account, the region, and the rough cost/runtime, then wait for the user's explicit "yes" before running.** `test` and `dns` need no confirmation.
4. Run from Git Bash: `bash scripts/eks.sh $1` (default `all`). Stream the output.
5. On success, report the endpoint(s) the script prints — `UI (ELB DNS): http://<lb>:4318` and, when DNS is configured, `UI (DNS): http://<name>:4318`.
6. Remind the user that the cluster keeps billing until `/eks teardown`.
