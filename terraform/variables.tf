variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "environment" {
  type    = string
  default = "demo"
}

variable "cluster_name" {
  type    = string
  default = "kube-rag-langg"
}

# Demo mode: minimal sizing, no RDS/ElastiCache, single node pool, scale-to-zero friendly.
variable "demo_mode" {
  type        = bool
  default     = true
  description = "Optimize for intermittent demos — smaller instances, optional RDS, no ElastiCache."
}

variable "vpc_cidr" {
  type    = string
  default = "10.0.0.0/16"
}

variable "kubernetes_version" {
  type    = string
  default = "1.29"
}

variable "node_instance_types" {
  type    = list(string)
  default = ["t3.large"]
}

variable "agent_node_instance_types" {
  type    = list(string)
  default = ["t3.large"]
}

variable "node_min_size" {
  type    = number
  default = 0
}

variable "node_max_size" {
  type    = number
  default = 2
}

variable "node_desired_size" {
  type    = number
  default = 1
}

variable "agent_node_desired_size" {
  type    = number
  default = 0
}

variable "enable_rds" {
  type        = bool
  default     = false
  description = "RDS Postgres — off by default in demo (agents use in-memory checkpoints)."
}

variable "enable_elasticache" {
  type        = bool
  default     = false
  description = "ElastiCache Redis — off by default; use in-cluster Redis when demo is running."
}

variable "postgres_version" {
  type    = string
  default = "16.4"
}

variable "postgres_instance_class" {
  type    = string
  default = "db.t4g.micro"
}

variable "postgres_allocated_storage" {
  type    = number
  default = 20
}

variable "postgres_max_allocated_storage" {
  type    = number
  default = 50
}

variable "postgres_username" {
  type      = string
  sensitive = true
  default   = "langgraph_admin"
}

variable "postgres_password" {
  type      = string
  sensitive = true
  default   = ""
}

variable "redis_node_type" {
  type    = string
  default = "cache.t4g.micro"
}

variable "langsmith_api_key" {
  type      = string
  sensitive = true
  default   = ""
}

variable "openai_api_key" {
  type      = string
  sensitive = true
  default   = ""
}

variable "tags" {
  type    = map(string)
  default = {}
}
