# VPC Module (`terraform-aws-vpc`)

Creates a multi-AZ VPC with public and private subnets, NAT gateway options, and VPC flow logs.

## Inputs

- `cidr_block` — primary CIDR (default 10.0.0.0/16)
- `az_count` — number of AZs (2-3 recommended)
- `enable_nat_gateway` — boolean

## Outputs

- `vpc_id`
- `private_subnet_ids`
- `public_subnet_ids`

Security groups (SG) are **not** created by this module; attach separately.
