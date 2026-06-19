# Deprecated — use CDK instead

This Terraform configuration has been superseded by the AWS CDK project in `../cdk/`.

**Use these commands instead:**

```bash
./scripts/infra-deploy.sh   # replaces terraform apply
./scripts/demo-park.sh      # replaces terraform destroy
```

Scripts read outputs from the CloudFormation stack `KubeRagLanggDemo` via `scripts/cdk-outputs.sh`.
