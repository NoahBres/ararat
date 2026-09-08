{ pkgs, config, ... }:
let
  cloudflaredStart = pkgs.writeShellScript "cloudflared-start" ''
    exec ${pkgs.cloudflared}/bin/cloudflared tunnel run \
      --token "$(cat /etc/cloudflared/tunnel-token)"
  '';
  araratatStart = pkgs.writeShellScript "ararat-start" ''
    ${pkgs.git}/bin/git pull --ff-only
    ${pkgs.tmux}/bin/tmux kill-session -t ararat 2>/dev/null || true
    ${pkgs.tmux}/bin/tmux new-session -d -s ararat -x 80 -y 24 ./claude-telegram.sh
    trap '${pkgs.tmux}/bin/tmux kill-session -t ararat 2>/dev/null; exit 0' TERM INT
    while ${pkgs.tmux}/bin/tmux has-session -t ararat 2>/dev/null; do
      sleep 5
    done
    exit 1
  '';
  hometoolsStart = pkgs.writeShellScript "hometools-start" ''
    set -euo pipefail
    cd "${config.home.homeDirectory}/Developer/ararat/hometools"
    if [ ! -f "${config.home.homeDirectory}/.config/hometools/env" ]; then
      echo "hometools: missing ${config.home.homeDirectory}/.config/hometools/env -- see setup comment in home.nix" >&2
      exit 1
    fi
    set -a
    source "${config.home.homeDirectory}/.config/hometools/env"
    set +a
    exec ${pkgs.uv}/bin/uv run --frozen hometools serve --host 127.0.0.1 --port 8787
  '';
in
{
  imports = [ ../common/darwin/home.nix ];

  home.packages = with pkgs; [
    ffmpeg
  ];

  home.shellAliases = {
    "attach-ararat" = "tmux attach-session -t ararat";
    "restart-ararat" = "launchctl kickstart -k gui/$UID/com.noahbres.ararat";
    "kill-ararat" = "launchctl kill SIGTERM gui/$UID/com.noahbres.ararat";
    "restart-hometools" = "launchctl kickstart -k gui/$UID/com.noahbres.hometools";
    "kill-hometools" = "launchctl kill SIGTERM gui/$UID/com.noahbres.hometools";
    "hometools-log" = "tail -f /tmp/hometools.log /tmp/hometools-error.log";
  };

  # cloudflared tunnel token must be manually deployed to the machine before activating:
  #   sudo mkdir -p /etc/cloudflared
  #   echo "your-tunnel-token-here" | sudo tee /etc/cloudflared/tunnel-token
  #   sudo chmod 600 /etc/cloudflared/tunnel-token
  # Token is available in Cloudflare Zero Trust → Networks → Tunnels → <tunnel> → Configure → Install connector
  launchd.agents.cloudflared = {
    enable = true;
    config = {
      Label = "com.noahbres.cloudflared";
      ProgramArguments = [ "${cloudflaredStart}" ];
      RunAtLoad = true;
      KeepAlive = {
        SuccessfulExit = false;
      };
      ThrottleInterval = 5;
      StandardOutPath = "/tmp/cloudflared.log";
      StandardErrorPath = "/tmp/cloudflared-error.log";
    };
  };

  launchd.agents.things-today-tracker = {
    enable = true;
    config = {
      Label = "com.noahbres.things-today-tracker";
      ProgramArguments = [
        "/usr/bin/python3"
        "${config.home.homeDirectory}/Developer/ararat/tools/things-today-tracker.py"
      ];
      WorkingDirectory = "${config.home.homeDirectory}/Developer/ararat";
      EnvironmentVariables = {
        PATH = "/opt/homebrew/bin:/etc/profiles/per-user/noah/bin:/run/current-system/sw/bin:/nix/var/nix/profiles/default/bin:/usr/bin:/bin";
        LANG = "en_US.UTF-8";
        LC_ALL = "en_US.UTF-8";
      };
      StartCalendarInterval = [
        {
          Hour = 9;
          Minute = 7;
        }
      ];
      StandardOutPath = "/tmp/things-today-tracker.log";
      StandardErrorPath = "/tmp/things-today-tracker-error.log";
    };
  };

  launchd.agents.ararat = {
    enable = true;
    config = {
      Label = "com.noahbres.ararat";
      ProgramArguments = [ "${araratatStart}" ];
      WorkingDirectory = "${config.home.homeDirectory}/Developer/ararat";
      EnvironmentVariables = {
        PATH = "/opt/homebrew/bin:/etc/profiles/per-user/noah/bin:/run/current-system/sw/bin:/nix/var/nix/profiles/default/bin:/usr/bin:/bin";
        TERM = "xterm-256color";
        COLORTERM = "truecolor";
        LANG = "en_US.UTF-8";
        LC_ALL = "en_US.UTF-8";
        LC_CTYPE = "en_US.UTF-8";
      };
      KeepAlive = {
        SuccessfulExit = false;
      };
      ThrottleInterval = 5;
      RunAtLoad = true;
      StandardOutPath = "/tmp/ararat.log";
      StandardErrorPath = "/tmp/ararat-error.log";
    };
  };

  # hometools -- personal API + MCP server (api.noahbres.com / mcp.noahbres.com).
  # See notes/plans/hometools-api.md for the full design.
  #
  # One-time setup on rtk before this agent will start successfully:
  #   1. `uv python install 3.12` (pins the interpreter path the FDA grant in
  #      plan §6.1 depends on -- don't bump the patch version casually).
  #   2. Create ~/.config/hometools/env (chmod 600) with:
  #        HOMETOOLS_BEARER_TOKEN=<openssl rand -hex 32>
  #        HOMETOOLS_MCP_SECRET=<openssl rand -hex 24>   # url-safe, no slashes
  #        CF_ACCESS_TEAM_DOMAIN=<team>.cloudflareaccess.com
  #        CF_ACCESS_AUD=<aud tag from the Cloudflare Access app for api.noahbres.com>
  #        THINGS_AUTH_TOKEN=<same value as in ~/Developer/ararat/.env>
  #        IMESSAGE_WRITE_ENABLED=false
  #        IMESSAGE_WRITE_ALLOWLIST=            # comma-separated phone/email identifiers
  #      Also save the bearer token and MCP secret to 1Password (item `hometools`).
  #   3. Full Disk Access (only needed for iMessage tools, not Things): System
  #      Settings -> Privacy & Security -> Full Disk Access -> `+` -> press
  #      Cmd+Shift+G and paste the path from `uv python find 3.12` inside
  #      `hometools/` (something like
  #      ~/.local/share/uv/python/cpython-3.12.x-macos-aarch64-none/bin/python3.12)
  #      -> enable. Then `restart-hometools`. Must be done via Screen Sharing
  #      on rtk (this is a TCC/GUI step, not something an agent can do).
  launchd.agents.hometools = {
    enable = true;
    config = {
      Label = "com.noahbres.hometools";
      ProgramArguments = [ "${hometoolsStart}" ];
      WorkingDirectory = "${config.home.homeDirectory}/Developer/ararat/hometools";
      EnvironmentVariables = {
        PATH = "/opt/homebrew/bin:/etc/profiles/per-user/noah/bin:/run/current-system/sw/bin:/nix/var/nix/profiles/default/bin:/usr/bin:/bin";
        LANG = "en_US.UTF-8";
        LC_ALL = "en_US.UTF-8";
      };
      KeepAlive = {
        SuccessfulExit = false;
      };
      ThrottleInterval = 5;
      RunAtLoad = true;
      StandardOutPath = "/tmp/hometools.log";
      StandardErrorPath = "/tmp/hometools-error.log";
    };
  };
}
