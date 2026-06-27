# EKS Cluster Module

Provisions control plane + managed node groups with IRSA enabled.

## Requirements

- Existing VPC with private subnets tagged `kubernetes.io/role/internal-elb=1`
- IAM role for cluster with `AmazonEKSClusterPolicy`

Outputs include `oidc_provider_arn` for service account roles.

See also VPC module readme for subnet layout.
