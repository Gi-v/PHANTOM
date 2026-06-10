# Vault Kubernetes Auth Configuration for PHANTOM
# Run these commands after Vault is deployed in-cluster

# 1. Enable Kubernetes auth method
vault auth enable kubernetes

# 2. Configure it with the cluster's API server
vault write auth/kubernetes/config \
  kubernetes_host="https://kubernetes.default.svc" \
  kubernetes_ca_cert=@/var/run/secrets/kubernetes.io/serviceaccount/ca.crt \
  token_reviewer_jwt=@/var/run/secrets/kubernetes.io/serviceaccount/token \
  issuer="https://kubernetes.default.svc.cluster.local"

# 3. Create phantom app role
vault write auth/kubernetes/role/phantom-ml \
  bound_service_account_names=phantom-ml \
  bound_service_account_namespaces=phantom \
  policies=phantom-app \
  ttl=1h

vault write auth/kubernetes/role/phantom-controller \
  bound_service_account_names=phantom-controller \
  bound_service_account_namespaces=phantom \
  policies=phantom-app \
  ttl=1h

# 4. Seed secrets (replace with real values before use)
vault kv put secret/phantom/config \
  openai_api_key="sk-..." \
  prometheus_url="http://prometheus:9090" \
  tempo_url="http://tempo:3200"

vault kv put secret/phantom/ml-keys \
  model_signing_key="change-me-before-use"
