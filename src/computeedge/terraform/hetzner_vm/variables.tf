variable "hcloud_token" {
  type      = string
  sensitive = true
}

variable "server_name" {
  type = string
}

variable "server_type" {
  type = string
}

variable "location" {
  type    = string
  default = "nbg1"
}

variable "image" {
  type    = string
  default = "ubuntu-24.04"
}

variable "ssh_public_key" {
  type = string
}
