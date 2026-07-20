output "database_url" {
  description = "Set as the backend's DATABASE_URL secret."
  sensitive   = true
  value       = "postgresql+psycopg://${var.db_username}:${var.db_password}@${aws_db_instance.postgres.address}:5432/${var.db_name}"
}

output "redis_url" {
  description = "Set as the backend's REDIS_URL (TLS endpoint)."
  value       = "rediss://${aws_elasticache_replication_group.redis.primary_endpoint_address}:6379/0"
}

output "backup_bucket" {
  description = "S3 bucket for backups (backups/ prefix) and datasets."
  value       = aws_s3_bucket.data.bucket
}

output "postgres_address" {
  value = aws_db_instance.postgres.address
}
