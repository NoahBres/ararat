{
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";

    nix-darwin.url = "github:nix-darwin/nix-darwin";
    nix-darwin.inputs.nixpkgs.follows = "nixpkgs";

    home-manager.url = "github:nix-community/home-manager";
    home-manager.inputs.nixpkgs.follows = "nixpkgs";

    googleworkspace-cli.url = "github:googleworkspace/cli";

    llm-agents.url = "github:numtide/llm-agents.nix";
    llm-agents.inputs.nixpkgs.follows = "nixpkgs";

    determinate.url = "https://flakehub.com/f/DeterminateSystems/determinate/3";

    git-hooks.url = "github:cachix/git-hooks.nix";

    deploy-rs.url = "github:serokell/deploy-rs";
    deploy-rs.inputs.nixpkgs.follows = "nixpkgs";
  };

  outputs =
    inputs@{
      self,
      nixpkgs,
      nix-darwin,
      home-manager,
      googleworkspace-cli,
      llm-agents,
      determinate,
      git-hooks,
      deploy-rs,
      ...
    }:
    {
      # Build darwin flake using:
      # $ darwin-rebuild build --flake .#rnn
      darwinConfigurations."rnn" = nix-darwin.lib.darwinSystem {
        modules = [
          { nixpkgs.overlays = [ llm-agents.overlays.shared-nixpkgs ]; }
          determinate.darwinModules.default
          ./hosts/rnn/configuration.nix
          home-manager.darwinModules.home-manager
        ];
        specialArgs = { inherit inputs; };
      };

      darwinConfigurations."rtk" = nix-darwin.lib.darwinSystem {
        modules = [
          { nixpkgs.overlays = [ llm-agents.overlays.shared-nixpkgs ]; }
          determinate.darwinModules.default
          ./hosts/rtk/configuration.nix
          home-manager.darwinModules.home-manager
        ];
        specialArgs = { inherit inputs; };
      };

      formatter.aarch64-darwin = nixpkgs.legacyPackages.aarch64-darwin.nixfmt-tree;

      # Remote deploy to the headless Mac mini: `just deploy-rtk` (see justfile).
      # Builds the rtk closure locally, copies it over the Cloudflare-tunnel SSH
      # alias, activates with interactive sudo (no NOPASSWD needed), and rolls
      # back automatically if it can't reconnect afterwards (magic rollback).
      deploy.nodes.rtk = {
        hostname = "rtk-cloudflare"; # ssh alias from hosts/common/darwin/home.nix
        sshUser = "noah";
        user = "root";
        interactiveSudo = true;
        remoteBuild = false;
        profiles.system.path = deploy-rs.lib.aarch64-darwin.activate.darwin self.darwinConfigurations.rtk;
      };

      packages.aarch64-darwin.deploy-rs = nixpkgs.legacyPackages.aarch64-darwin.deploy-rs; # binary-cached, same CLI

      checks.aarch64-darwin = {
        pre-commit-check = git-hooks.lib.aarch64-darwin.run {
          src = ./.;
          hooks.nixfmt.enable = true;
        };
      }
      // (deploy-rs.lib.aarch64-darwin.deployChecks self.deploy);

      devShells.aarch64-darwin.default =
        let
          pkgs = nixpkgs.legacyPackages.aarch64-darwin;
        in
        pkgs.mkShell {
          inherit (self.checks.aarch64-darwin.pre-commit-check) shellHook;
        };
    };
}
