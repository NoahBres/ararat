# Root justfile for the ararat repo.
#
# nixos-config/justfile is imported here, so its recipes are available from the
# repo root with the same names (`just deploy-rtk`, `just build-rtk`, ...).
# They use `source_directory()` for the flake path, so they resolve to
# nixos-config/ no matter where they're invoked from — one file, can't drift.
#
#   just --list          # list everything, including the nix recipes
#   just build-rtk
#   just build-deploy-rtk

import 'nixos-config/justfile'

# Default: show everything.
default:
  @just --list
