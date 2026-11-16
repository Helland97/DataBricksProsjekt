config {
  call_module_type = "none"   # no child modules in this repo
}

plugin "terraform" {
  enabled = true
  preset  = "recommended"     # unused variables, missing types, deprecated syntax, ...
}

plugin "azurerm" {
  enabled = true
  version = "0.29.0"          # check the latest release at github.com/terraform-linters/tflint-ruleset-azurerm
  source  = "github.com/terraform-linters/tflint-ruleset-azurerm"
}
