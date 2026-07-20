terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # Configure a remote backend for team use (state locking). Example:
  # backend "s3" {
  #   bucket         = "acme-terraform-state"
  #   key            = "datawhisper/prod.tfstate"
  #   region         = "us-east-1"
  #   dynamodb_table = "terraform-locks"
  #   encrypt        = true
  # }
}

provider "aws" {
  region = var.aws_region
  default_tags {
    tags = {
      Project     = "datawhisper"
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}
