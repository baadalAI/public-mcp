resource "hcloud_ssh_key" "computeedge" {
  name       = "${var.server_name}-ssh"
  public_key = var.ssh_public_key
}

resource "hcloud_firewall" "computeedge" {
  name = "${var.server_name}-firewall"

  rule {
    direction  = "in"
    protocol   = "icmp"
    source_ips = ["0.0.0.0/0", "::/0"]
  }

  rule {
    direction  = "in"
    protocol   = "tcp"
    port       = "22"
    source_ips = ["0.0.0.0/0", "::/0"]
  }

  rule {
    direction  = "in"
    protocol   = "tcp"
    port       = "80"
    source_ips = ["0.0.0.0/0", "::/0"]
  }

  rule {
    direction  = "in"
    protocol   = "tcp"
    port       = "443"
    source_ips = ["0.0.0.0/0", "::/0"]
  }
}

resource "hcloud_server" "app" {
  name         = var.server_name
  server_type  = var.server_type
  image        = var.image
  location     = var.location
  ssh_keys     = [hcloud_ssh_key.computeedge.id]
  firewall_ids = [hcloud_firewall.computeedge.id]

  labels = {
    managed_by = "computeedge"
    backend    = "terraform"
  }
}
