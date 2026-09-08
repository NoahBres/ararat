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

  # sshd hardening. rtk's SSH port is reachable from the internet through the
  # Cloudflare tunnel (ssh-rtk.noahbres.com, behind a Cloudflare Access app),
  # so the server itself must only ever accept keys. macOS's sshd_config does
  # `Include /etc/ssh/sshd_config.d/*`, and nix-darwin already owns
  # `100-nix-darwin.conf` in that directory (this option is its contents), so
  # no extra environment.etc file is needed. Key auth is unaffected: Noah's key
  # is in ~/.ssh/authorized_keys on rtk. Verify after deploy with
  #   ssh rtk 'sudo sshd -T | grep -i passwordauth'
  # `enable` is deliberately left at null (macOS keeps managing Remote Login).
  services.openssh.extraConfig = ''
    PasswordAuthentication no
    KbdInteractiveAuthentication no
    ChallengeResponseAuthentication no
    PermitRootLogin no
  '';
}
