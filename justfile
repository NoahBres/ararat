# Root justfile for the ararat repo.
#
# nixos-config has its own justfile whose recipes must run from that directory
# (they use `--flake .#<host>`). Exposing it as a module preserves that: every
# `just nix <recipe>` runs with nixos-config/ as the working directory.
#
#   just nix                 # list the nix-darwin recipes
#   just nix build-rtk
#   just nix build-deploy-rtk

mod nix 'nixos-config'

# Default: show everything, including module recipes.
default:
  @just --list --list-submodules
