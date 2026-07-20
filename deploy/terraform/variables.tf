variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "environment" {
  type    = string
  default = "prod"
}

# Networking is environment-specific — supply an existing VPC and its private
# subnets rather than having this module own the network.
variable "vpc_id" {
  type        = string
  description = "VPC to place RDS and ElastiCache into."
}

variable "private_subnet_ids" {
  type        = list(string)
  description = "Private subnets (>=2 AZs) for the DB and cache subnet groups."
}

variable "app_security_group_id" {
  type        = string
  description = "Security group of the backend pods/nodes allowed to reach the DB and cache."
}

variable "db_name" {
  type    = string
  default = "datawhisper"
}

variable "db_username" {
  type    = string
  default = "datawhisper"
}

variable "db_password" {
  type        = string
  sensitive   = true
  description = "Master password for Postgres. Source from a secrets manager, not source control."
}

variable "db_instance_class" {
  type    = string
  default = "db.t4g.medium"
}

variable "db_allocated_storage" {
  type    = number
  default = 50
}

variable "redis_node_type" {
  type    = string
  default = "cache.t4g.small"
}
