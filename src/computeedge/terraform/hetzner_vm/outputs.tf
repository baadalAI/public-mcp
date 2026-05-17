output "server_id" {
  value = hcloud_server.app.id
}

output "public_ipv4" {
  value = hcloud_server.app.ipv4_address
}

output "ssh_key_id" {
  value = hcloud_ssh_key.computeedge.id
}

output "firewall_id" {
  value = hcloud_firewall.computeedge.id
}

output "server_status" {
  value = hcloud_server.app.status
}
