terraform {
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
  }
}

variable "cluster_name"    { default = "phantom-cluster" }
variable "cluster_version" { default = "1.29" }
variable "region"          { default = "us-east-1" }
variable "node_groups"     { type = map(any) }

data "aws_availability_zones" "available" {}

module "vpc" {
  source          = "terraform-aws-modules/vpc/aws"
  version         = "~> 5.0"
  name            = "${var.cluster_name}-vpc"
  cidr            = "10.0.0.0/16"
  azs             = slice(data.aws_availability_zones.available.names, 0, 3)
  private_subnets = ["10.0.1.0/24", "10.0.2.0/24", "10.0.3.0/24"]
  public_subnets  = ["10.0.101.0/24", "10.0.102.0/24", "10.0.103.0/24"]
  enable_nat_gateway   = true
  single_nat_gateway   = true
  enable_dns_hostnames = true
  tags = {
    "kubernetes.io/cluster/${var.cluster_name}" = "shared"
    project = "phantom"
  }
}

module "eks" {
  source          = "terraform-aws-modules/eks/aws"
  version         = "~> 20.0"
  cluster_name    = var.cluster_name
  cluster_version = var.cluster_version
  vpc_id          = module.vpc.vpc_id
  subnet_ids      = module.vpc.private_subnets
  cluster_endpoint_public_access = true
  eks_managed_node_groups = var.node_groups
  tags = { project = "phantom" }
}

output "cluster_name"     { value = module.eks.cluster_name }
output "cluster_endpoint" { value = module.eks.cluster_endpoint }
output "kubeconfig_cmd"   { value = "aws eks update-kubeconfig --region ${var.region} --name ${var.cluster_name}" }
