{ ... }:
{
  imports = [ ../common/darwin/configuration.nix ];

  home-manager.users.noah = import ./home.nix;

  determinateNix.customSettings = {
    extra-trusted-public-keys = [
      "rnn:WwyJul2LMpCUu2Pm33omQYvDpC5tCXLQL89Ow7GIdbY="
    ];
    require-sigs = false;
  };

  # deploy-rs's magic-rollback makes two separate SSH-spawned sudo calls per
  # deploy (activate, then a confirmation "wait"). macOS sudo defaults to
  # tty_tickets, which scopes the cached credential to the exact pty of each
  # ssh invocation, so the password from the first call never covers the
  # second -- it just silently reprompts (empty -p "") and looks like a hung
  # confirmation. Disabling tty_tickets makes the credential cache
  # user-scoped instead (same behavior as normal sudo caching, just not
  # pty-bound) so the second call reuses it. Still requires the password once
  # -- not passwordless sudo.
  environment.etc."sudoers.d/deploy-rs-tty-tickets".text = "Defaults !tty_tickets\n";
}
