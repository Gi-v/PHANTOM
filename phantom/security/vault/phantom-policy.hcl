# PHANTOM app policy
path "secret/data/phantom/*" {
  capabilities = ["read"]
}
path "secret/data/phantom/ml-keys" {
  capabilities = ["read"]
}
path "database/creds/phantom-db" {
  capabilities = ["read"]
}
