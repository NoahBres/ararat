{ pkgs, config, ... }:
let
  cloudflaredStart = pkgs.writeShellScript "cloudflared-start" ''
    exec ${pkgs.cloudflared}/bin/cloudflared tunnel run \
      --token "$(cat /etc/cloudflared/tunnel-token)"
  '';
  rtkApiStart = pkgs.writeShellScript "rtk-api-start" ''
    set -euo pipefail
    cd "${config.home.homeDirectory}/Developer/ararat/rtk-api"
    if [ ! -f "${config.home.homeDirectory}/.config/rtk-api/env" ]; then
      echo "rtk-api: missing ${config.home.homeDirectory}/.config/rtk-api/env -- see setup comment in home.nix" >&2
      exit 1
    fi
    set -a
    source "${config.home.homeDirectory}/.config/rtk-api/env"
    set +a
    exec ${pkgs.uv}/bin/uv run --frozen rtk-api serve --host 127.0.0.1 --port 8787
  '';
in
{
  imports = [ ../common/darwin/home.nix ];

  home.packages = with pkgs; [
    ffmpeg
  ];

  home.shellAliases = {
    "restart-rtk-api" = "launchctl kickstart -k gui/$UID/com.noahbres.rtk-api";
    "kill-rtk-api" = "launchctl kill SIGTERM gui/$UID/com.noahbres.rtk-api";
    "rtk-api-log" = "tail -f /tmp/rtk-api.log /tmp/rtk-api-error.log";
  };

  # cloudflared tunnel token must be manually deployed to the machine before activating:
  #   sudo mkdir -p /etc/cloudflared
  #   echo "your-tunnel-token-here" | sudo tee /etc/cloudflared/tunnel-token
  #   sudo chmod 600 /etc/cloudflared/tunnel-token
  # Token is available in Cloudflare Zero Trust → Networks → Tunnels → <tunnel> → Configure → Install connector
  launchd.agents.cloudflared = {
    enable = true;
    waitForNixStore = false; # show as "cloudflared" (not "sh") in Login Items; gui agents start after /nix/store is mounted anyway
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
    waitForNixStore = false; # show as "things-today-tracker" (not "sh") in Login Items; gui agents start after /nix/store is mounted anyway
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

  # trash-reminder -- roommate trash pickup reminder for the groupchat.
  # See tools/trash-reminder.py. The script decides from the date whether
  # tonight is due (provisional heads-up 2 nights before pickup, final
  # reminder 1 night before) and no-ops otherwise, so all four slots are
  # listed and only the due one fires:
  #   normal week (Tue pickup): Sun 20:00 provisional, Mon 19:00 final
  #   slip week   (Wed pickup): Mon 20:00 provisional, Tue 19:00 final
  # Runs through ~/Applications/trash-reminder.app (tools/trash-launcher/,
  # built once on rtk) so the Full Disk Access grant is scoped to
  # "trash-reminder" instead of system-wide /usr/bin/python3. Grant it over
  # Screen Sharing, then run tools/trash-reminder.py --probe first — it
  # triggers the Automation prompt without sending anything.
  launchd.agents.trash-reminder = {
    enable = true;
    waitForNixStore = false; # show as "trash-reminder" (not "sh") in Login Items; gui agents start after /nix/store is mounted anyway
    config = {
      Label = "com.noahbres.trash-reminder";
      # Makes System Settings > General > Login Items list this as
      # "trash-reminder" (the launcher app's bundle) instead of "sh".
      AssociatedBundleIdentifiers = [ "com.noahbres.trash-reminder" ];
      ProgramArguments = [
        "${config.home.homeDirectory}/Applications/trash-reminder.app/Contents/MacOS/trash-reminder"
        "/usr/bin/python3"
        "${config.home.homeDirectory}/Developer/ararat/tools/trash-reminder.py"
      ];
      WorkingDirectory = "${config.home.homeDirectory}/Developer/ararat";
      EnvironmentVariables = {
        PATH = "/opt/homebrew/bin:/etc/profiles/per-user/noah/bin:/run/current-system/sw/bin:/nix/var/nix/profiles/default/bin:/usr/bin:/bin";
        LANG = "en_US.UTF-8";
        LC_ALL = "en_US.UTF-8";
      };
      StartCalendarInterval = [
        {
          Weekday = 0;
          Hour = 20;
          Minute = 0;
        } # Sun 8pm: provisional (normal weeks)
        {
          Weekday = 1;
          Hour = 19;
          Minute = 0;
        } # Mon 7pm: final (normal weeks)
        {
          Weekday = 1;
          Hour = 20;
          Minute = 0;
        } # Mon 8pm: provisional (slip weeks)
        {
          Weekday = 2;
          Hour = 19;
          Minute = 0;
        } # Tue 7pm: final (slip weeks)
      ];
      StandardOutPath = "/tmp/trash-reminder.log";
      StandardErrorPath = "/tmp/trash-reminder-error.log";
    };
  };

  # trash-skip-watcher -- instant acks for "bot skip" commands.
  # Companion to trash-reminder: runs every 5 minutes, scans the groupchat
  # for new messages, and replies [BOT] to skip commands during the active
  # window (1-2 nights before pickup). Read-only outside the window.
  # Runs through the same trash-reminder.app launcher (see above), so it is
  # covered by the same scoped Full Disk Access grant. First run initializes
  # its cursor so history never replays.
  launchd.agents.trash-skip-watcher = {
    enable = true;
    waitForNixStore = false; # show as "trash-skip-watcher" (not "sh") in Login Items; gui agents start after /nix/store is mounted anyway
    config = {
      Label = "com.noahbres.trash-skip-watcher";
      AssociatedBundleIdentifiers = [ "com.noahbres.trash-reminder" ];
      ProgramArguments = [
        "${config.home.homeDirectory}/Applications/trash-reminder.app/Contents/MacOS/trash-reminder"
        "/usr/bin/python3"
        "${config.home.homeDirectory}/Developer/ararat/tools/trash-skip-watcher.py"
      ];
      WorkingDirectory = "${config.home.homeDirectory}/Developer/ararat";
      EnvironmentVariables = {
        PATH = "/opt/homebrew/bin:/etc/profiles/per-user/noah/bin:/run/current-system/sw/bin:/nix/var/nix/profiles/default/bin:/usr/bin:/bin";
        LANG = "en_US.UTF-8";
        LC_ALL = "en_US.UTF-8";
      };
      StartInterval = 300;
      StandardOutPath = "/tmp/trash-skip-watcher.log";
      StandardErrorPath = "/tmp/trash-skip-watcher-error.log";
    };
  };

  # rtk-api -- personal API + MCP server (api.noahbres.com / mcp.noahbres.com).
  # See notes/plans/rtk-api.md for the full design.
  #
  # One-time setup on rtk before this agent will start successfully:
  #   1. `uv python install 3.12` (pins the interpreter path the FDA grant in
  #      plan §6.1 depends on -- don't bump the patch version casually).
  #   2. Create ~/.config/rtk-api/env (chmod 600) with:
  #        RTK_API_BEARER_TOKEN=<openssl rand -hex 32>
  #        RTK_API_MCP_SECRET=<openssl rand -hex 24>   # url-safe, no slashes
  #        CF_ACCESS_TEAM_DOMAIN=<team>.cloudflareaccess.com
  #        CF_ACCESS_AUD=<aud tag from the Cloudflare Access app for api.noahbres.com>
  #        THINGS_AUTH_TOKEN=<same value as in ~/Developer/ararat/.env>
  #        IMESSAGE_WRITE_ENABLED=false
  #        IMESSAGE_WRITE_ALLOWLIST=            # comma-separated phone/email identifiers
  #      Also save the bearer token and MCP secret to 1Password (item `rtk-api`).
  #   3. Build the launcher app: `~/Developer/ararat/rtk-api/launcher/build.sh`
  #      (creates ~/Applications/rtk-api.app; do this BEFORE deploying this
  #      agent or launchd will fail to start it).
  #   4. Full Disk Access (covers Things group container, chat.db, AddressBook):
  #      System Settings -> Privacy & Security -> Full Disk Access -> `+` ->
  #      pick ~/Applications/rtk-api.app -> enable. Then `restart-rtk-api`.
  #      Must be done via Screen Sharing on rtk (TCC/GUI step).
  launchd.agents.rtk-api = {
    enable = true;
    waitForNixStore = false; # show as "rtk-api" (not "sh") in Login Items; gui agents start after /nix/store is mounted anyway
    config = {
      Label = "com.noahbres.rtk-api";
      # Makes System Settings > General > Login Items list this as "rtk-api"
      # (the launcher app's bundle) instead of "sh" (home-manager wraps every
      # agent in /bin/sh -c 'wait4path ... && exec ...').
      AssociatedBundleIdentifiers = [ "com.noahbres.rtk-api" ];
      # Run via the rtk-api.app launcher so macOS attributes TCC permissions
      # (Full Disk Access, "access data from other apps", Automation) to a
      # named app instead of a generic python/uv binary. Build once on rtk:
      #   ~/Developer/ararat/rtk-api/launcher/build.sh
      ProgramArguments = [
        "${config.home.homeDirectory}/Applications/rtk-api.app/Contents/MacOS/rtk-api"
        "${rtkApiStart}"
      ];
      WorkingDirectory = "${config.home.homeDirectory}/Developer/ararat/rtk-api";
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
      StandardOutPath = "/tmp/rtk-api.log";
      StandardErrorPath = "/tmp/rtk-api-error.log";
    };
  };
}
