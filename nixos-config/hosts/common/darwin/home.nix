{
  pkgs,
  lib,
  inputs,
  ...
}:
let
  mkOpenRouterWrapper =
    name: opusModel: sonnetModel: haikuModel:
    pkgs.writeShellScriptBin name ''
      OPENROUTER_API_KEY=$(${pkgs._1password-cli}/bin/op read "op://Private/OpenRouter Key - Claude/password")
      export OPENROUTER_API_KEY
      export ANTHROPIC_BASE_URL="https://openrouter.ai/api"
      export ANTHROPIC_AUTH_TOKEN="$OPENROUTER_API_KEY"
      export ANTHROPIC_API_KEY=""
      export ANTHROPIC_DEFAULT_OPUS_MODEL="${opusModel}"
      export ANTHROPIC_DEFAULT_SONNET_MODEL="${sonnetModel}"
      export ANTHROPIC_DEFAULT_HAIKU_MODEL="${haikuModel}"
      export CLAUDE_CODE_SUBAGENT_MODEL="${sonnetModel}"
      exec ${pkgs.llm-agents.claude-code}/bin/claude --dangerously-skip-permissions "$@"
    '';

  cy-auto = mkOpenRouterWrapper "cy-auto" "openrouter/auto" "openrouter/auto" "openrouter/auto";
  cy-glm = mkOpenRouterWrapper "cy-glm" "z-ai/glm-5" "z-ai/glm-5" "z-ai/glm-5-turbo";
  cy-qwen =
    mkOpenRouterWrapper "cy-qwen" "qwen/qwen3.6-plus:free" "qwen/qwen3.6-plus:free"
      "qwen/qwen3.6-plus:free";
  cy-kimi =
    mkOpenRouterWrapper "cy-kimi" "moonshotai/kimi-k2.5" "moonshotai/kimi-k2.5"
      "moonshotai/kimi-k2.5";

  # ssh -> rtk: try the Tailscale link (rtk.local) first, fall back to the
  # Cloudflare Access tunnel otherwise. Asks the local tailscaled (via `tailscale
  # status`, an instant IPC call -- no network round trip) whether Tailscale is
  # up and rtk is currently online *before* touching the network, so we skip
  # straight to Cloudflare instead of eating a multi-second connect timeout
  # whenever Tailscale itself is off. Only when that heuristic looks good do we
  # spend a short bounded probe confirming rtk.local:22 actually answers.
  #
  # `--cloudflare` skips the Tailscale probe and goes straight to the tunnel
  # (used by the `rtk-cloudflare` alias so both aliases share one code path).
  #
  # ssh-rtk.noahbres.com sits behind a Cloudflare Access app ("ssh-rtk") that
  # admits either the owner's `rtk-api` service token or a browser login as
  # noahbres@gmail.com. cloudflared reads the service token from
  # TUNNEL_SERVICE_TOKEN_ID/_SECRET, so we pull those from 1Password (item
  # "rtk-api cloudflare access service token", fields client_id/credential)
  # to keep the tunnel path non-interactive for scripts like deploy-rs. If
  # the caller already exported them we leave them alone; if `op` is locked
  # or unavailable we fall through to cloudflared's interactive browser
  # login instead of failing.
  rtkSshProxy = pkgs.writeShellScript "rtk-ssh-proxy" ''
    set -euo pipefail

    cloudflare_fallback() {
      if [ -z "''${TUNNEL_SERVICE_TOKEN_ID:-}" ] || [ -z "''${TUNNEL_SERVICE_TOKEN_SECRET:-}" ]; then
        op_read() {
          ${pkgs.coreutils}/bin/timeout 30 ${pkgs._1password-cli}/bin/op read \
            "op://Private/rtk-api cloudflare access service token/$1" 2>/dev/null
        }
        if id=$(op_read client_id) && secret=$(op_read credential) \
          && [ -n "$id" ] && [ -n "$secret" ]; then
          export TUNNEL_SERVICE_TOKEN_ID="$id" TUNNEL_SERVICE_TOKEN_SECRET="$secret"
        else
          echo "rtk-ssh-proxy: could not read the Access service token from 1Password; falling back to browser login" >&2
        fi
      fi
      exec ${pkgs.cloudflared}/bin/cloudflared access ssh --hostname ssh-rtk.noahbres.com
    }

    if [ "''${1:-}" = "--cloudflare" ]; then
      cloudflare_fallback
    fi

    status=$(tailscale status --json 2>/dev/null) || cloudflare_fallback
    rtk_online=$(printf '%s' "$status" | ${pkgs.jq}/bin/jq -r '
      (.BackendState == "Running") as $up
      | ([.Peer // {} | to_entries[] | select(.value.HostName == "rtk") | .value.Online][0] // false) as $peerOnline
      | ($up and $peerOnline)
    ' 2>/dev/null) || rtk_online=false

    if [ "$rtk_online" = "true" ] && nc -z -w1 rtk.local 22 2>/dev/null; then
      exec nc rtk.local 22
    else
      cloudflare_fallback
    fi
  '';
  cy-ant =
    mkOpenRouterWrapper "cy-ant" "anthropic/claude-opus-4.6" "anthropic/claude-sonnet-4.6"
      "anthropic/claude-haiku-4.5";
in
{
  programs = {
    ghostty = {
      enable = true;
      package = null; # installed via homebrew
      settings = {
        theme = "Snazzy";
        background-opacity = 0.9;
        background-blur-radius = 40;
        shell-integration-features = "cursor,title,ssh-env";
        notify-on-command-finish = "unfocused";
        notify-on-command-finish-action = "bell,notify";
      };
    };

    tmux = {
      enable = true;
      mouse = true;
      terminal = "tmux-256color";
      extraConfig = ''
        set -gq utf8 on
        set -gq status-utf8 on
        set -ga terminal-overrides ",*:Tc"
      '';
    };

    direnv = {
      enable = true;
      nix-direnv.enable = true;
    };

    zoxide.enable = true;

    atuin.enable = true;

    ssh = {
      enable = true;
      enableDefaultConfig = false;
      settings = {
        "*" = {
          hashKnownHosts = false;
          userKnownHostsFile = "~/.ssh/known_hosts";
        };
        # Cloudflare tunnel only. Same proxy script as `rtk` below, forced onto
        # the tunnel path (which fetches the Access service token from 1Password).
        rtk-cloudflare = {
          hostname = "ssh-rtk.noahbres.com";
          user = "noah";
          proxyCommand = "${rtkSshProxy} --cloudflare";
          # deploy-rs makes several SSH calls in a row (activate, then a separate
          # sudo'd "wait" call for magic-rollback confirmation). Without multiplexing
          # each call gets a fresh pty, so sudo's credential cache from the first
          # password prompt doesn't carry to the next one -- it just silently
          # reprompts (empty -p "") and looks like a hung confirmation.
          controlMaster = "auto";
          controlPath = "~/.ssh/sockets/%r@%h-%p";
          controlPersist = "10m";
        };
        # Preferred alias: Tailscale (rtk.local) first, Cloudflare Access as fallback.
        rtk = {
          hostname = "rtk.local";
          user = "noah";
          proxyCommand = "${rtkSshProxy}";
          controlMaster = "auto";
          controlPath = "~/.ssh/sockets/%r@%h-%p";
          controlPersist = "10m";
        };
      };
    };

    zsh = {
      enable = true;

      enableCompletion = true;
      autosuggestion.enable = true;
      syntaxHighlighting.enable = true;

      initContent =
        let
          zshConfigEarlyInit = lib.mkOrder 500 ''
            # From FAQ: https://github.com/romkatv/powerlevel10k?tab=readme-ov-file#how-do-i-initialize-direnv-when-using-instant-prompt
            (( ''${+commands[direnv]} )) && emulate zsh -c "$(direnv export zsh)"

             if [[ -r "''${XDG_CACHE_HOME:-$HOME/.cache}/p10k-instant-prompt-''${(%):-%n}.zsh" ]]; then
               source "''${XDG_CACHE_HOME:-$HOME/.cache}/p10k-instant-prompt-''${(%):-%n}.zsh"
             fi

             (( ''${+commands[direnv]} )) && emulate zsh -c "$(direnv hook zsh)"
          '';
          zshConfig = lib.mkOrder 1000 ''
            source ${pkgs.zsh-powerlevel10k}/share/zsh-powerlevel10k/powerlevel10k.zsh-theme
            source ~/.p10k.zsh
          '';
        in
        lib.mkMerge [
          zshConfigEarlyInit
          zshConfig
        ];
    };
  };

  home = {
    packages = with pkgs; [
      _1password-cli # 1Password CLI (op)
      gh # GitHub CLI
      cloudflared # cloudflare tunnel client (Access to rtk over ssh-rtk.noahbres.com)

      just
      tree

      # p10k
      zsh-powerlevel10k
      meslo-lgs-nf

      # Nix LSP/formatter
      nixd
      nixfmt

      llm-agents.claude-code
      llm-agents.opencode
      llm-agents.agent-browser

      # Google CLI
      google-cloud-sdk
      inputs.googleworkspace-cli.packages.${pkgs.stdenv.hostPlatform.system}.default

      bun
      nodejs_24

      delta
      glow

      uv
      cargo
      rustc
      rust-analyzer

      cy-auto
      cy-glm
      cy-qwen
      cy-kimi
      cy-ant
    ];

    sessionPath = [ "/Users/noah/.bun/bin" ];

    shellAliases = {
      "cy" = "claude --dangerously-skip-permissions";
    };

    file.".p10k.zsh".text = builtins.readFile ../../../modules/zsh/.p10k.zsh;

    activation.bootstrapRepos = lib.hm.dag.entryAfter [ "writeBoundary" ] ''
      for repo in dot-claude nixos-config ararat; do
        if [ ! -d "$HOME/Developer/$repo" ]; then
          ${pkgs.git}/bin/git clone https://github.com/noahbres/$repo "$HOME/Developer/$repo"
        fi
      done
    '';

    activation.createSshSockets = lib.hm.dag.entryAfter [ "writeBoundary" ] ''
      mkdir -p "$HOME/.ssh/sockets"
      chmod 700 "$HOME/.ssh/sockets"
    '';

    stateVersion = "25.05";
  };
}
