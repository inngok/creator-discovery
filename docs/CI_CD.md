# CI/CD: GitHub Actions -> VPS systemd

Workflow: `.github/workflows/ci.yml` (CI and VPS deploy).

Every push/PR runs Python 3.11 regression tests and validates Compose. CI uses temporary Redis and CPU-only PyTorch. Production deployment runs only after tests pass, on `DEPLOY_BRANCH`, with `DEPLOY_ENABLED=true`.

The VPS runs Python directly in `/root/creator-discovery/venv`, managed by systemd. Docker and GHCR are not needed for this deployment.

## GitHub configuration

Settings -> Secrets and variables -> Actions:

| Type | Name | Value |
| --- | --- | --- |
| Secret | VPS_HOST | 116.118.6.81 |
| Secret | VPS_USER | root |
| Secret | VPS_SSH_KEY | Dedicated deployment private key |
| Secret | VPS_KNOWN_HOSTS | Verified VPS SSH host key entry |
| Variable | VPS_PORT | 22 |
| Variable | VPS_DEPLOY_PATH | /root/creator-discovery |
| Variable | DEPLOY_BRANCH | main |
| Variable | VPS_API_SERVICE | Actual API systemd unit name, including .service |
| Variable | DEPLOY_ENABLED | false until setup is verified, then true |

Create the `production` environment under Settings -> Environments. Set any desired branch restrictions there.

## VPS prerequisites

The SSH user must be able to restart the existing systemd services. The deployment script expects `crawler-worker.service`, `crawler-beat.service`, and the API unit specified in `VPS_API_SERVICE`. Redis continues running as its existing host service.

The existing repository needs a clean tracked working tree, a Git origin pointing to this repository, and permission to fetch it non-interactively. Private repositories require a Git credential or deploy key on the VPS; the Actions-to-VPS key does not automatically grant GitHub repository access.

The script requires Bash, Git, flock, systemctl and the existing `venv/bin/python`. Keep `.env`, databases, model files and the virtual environment in place. No Docker login or Compose change is needed. Do not commit the VPS `.env`.

To inspect API service names without printing environment secrets:

```bash
systemctl list-units --type=service --all 'crawler*' --no-pager
```

## Deployment behavior

The script locks deployments, verifies all unit names and local tracked changes, fetches the branch, and checks that its tip matches the exact tested SHA. It fast-forwards the checkout without resetting local changes, installs requirements into the existing virtual environment, checks dependencies and syntax, then restarts the three application units. It checks all three units are active and `/openapi.json` responds on localhost:8000.

A branch moving during CI causes the older deployment to fail before changing code; run the workflow for the latest commit. Divergent local commits also fail without resetting files.

This is an in-place deployment with a short interruption. Code and dependencies change before services restart. Installation or health failures do not automatically roll back; the log reports the previous commit. Back up production data before the first deployment. A rollback requires restoring the chosen code/dependencies and restarting the same units; it does not revert database changes. Service-active checks do not verify actual crawl task execution.

## First run

1. Push this workflow to main and let tests pass with deployment disabled.
2. Verify the API unit name and add VPS_API_SERVICE.
3. Verify the VPS working tree is clean and Git fetch works.
4. Set DEPLOY_ENABLED=true.
5. Actions -> CI and VPS deploy -> Run workflow -> main.

## Validation

```bash
bash -n scripts/deploy.sh
TESTING=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 python -m unittest discover -s tests -v
```
