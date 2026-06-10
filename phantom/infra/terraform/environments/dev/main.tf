provider "aws" { region = "us-east-1" }

terraform {
  backend "s3" {
    bucket = "phantom-tfstate"
    key    = "dev/terraform.tfstate"
    region = "us-east-1"
  }
}

module "eks" {
  source       = "../../modules/eks"
  cluster_name = "phantom-dev"
  node_groups = {
    workers = {
      instance_types = ["t3.medium"]
      min_size       = 2
      max_size       = 6
      desired_size   = 3
    }
  }
}
