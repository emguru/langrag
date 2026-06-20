# Cognito authentication

The showcase UI can be protected with **Amazon Cognito** at the **ALB** (HTTPS required).

## 1. Deploy the user pool (CDK)

Cognito is created with the main stack:

```bash
./scripts/infra-deploy.sh
```

Outputs (also via `./scripts/cdk-outputs.sh` helpers):

| Output | Purpose |
|--------|---------|
| `OutCognitoUserPoolId` | User pool ID |
| `OutCognitoHostedUiUrl` | Hosted UI base URL |
| `OutCognitoClientId` | App client for ALB auth |

Configure in `cdk/cdk.json`:

```json
"cognitoDomainPrefix": "kube-rag-langg-demo",
"appDomainName": "",
"certificateArn": ""
```

- **`cognitoDomainPrefix`** — globally unique in the region (default: `kube-rag-langg-demo`)
- **`appDomainName`** — your DNS name for the showcase (e.g. `research.demo.example.com`). When set, CDK creates an ACM certificate (DNS validation).
- **`certificateArn`** — optional; use an existing ACM cert instead of auto-create

## 2. Create demo users

```bash
./scripts/cognito-create-user.sh you@company.com
# or interactively:
./scripts/cognito-create-user.sh
```

Password policy: min 12 chars, upper + lower + digit.

Self-sign-up is **disabled** — only admin-created users.

## 3. Enable HTTPS + Cognito at the ALB (recommended for internal demos)

ALB `authenticate-cognito` requires **HTTPS** on a **domain you control**.

```bash
./scripts/enable-https-cognito.sh research.demo.example.com
```

Then:

1. Add ACM **DNS validation** CNAME(s) (see script output)
2. Wait for certificate status **ISSUED**
3. `./scripts/apply-showcase-access.sh`
4. Point your domain **CNAME** to the ALB hostname
5. `./scripts/cognito-create-user.sh you@company.com`

Users hit `https://your-domain/` → Cognito login → showcase UI.

See [SECURITY.md](./SECURITY.md) for the full security model.

## 4. Without a custom domain (current default)

- Cognito **user pool exists** and users can be created
- UI still uses a **public LoadBalancer** (HTTP) until `appDomainName` + issued ACM cert
- Good for internal demos: create users now, add HTTPS ingress when you have DNS

## Lifecycle

| Command | Cognito / ingress |
|---------|-------------------|
| `demo-stop.sh` | Deletes ALB ingress or NLB service; pool remains |
| `demo-start.sh` | Recreates ingress or NLB via `apply-showcase-access.sh` |
| `demo-park.sh` | Destroys pool (demo mode); recreate on next `infra-deploy.sh` |

## Troubleshooting

**Certificate pending** — complete ACM DNS validation, then re-run `./scripts/apply-showcase-access.sh`.

**Redirect loop** — ensure Cognito app client callback URL is `https://<appDomainName>/oauth2/idpresponse`.

**Domain prefix taken** — change `cognitoDomainPrefix` in `cdk.json` and redeploy.
