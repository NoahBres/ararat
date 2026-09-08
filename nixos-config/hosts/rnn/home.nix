{ ... }:
{
  imports = [ ../common/darwin/home.nix ];

  home.file.".hammerspoon".source = ../../modules/hammerspoon;
}
