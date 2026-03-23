# SPDX-FileCopyrightText: 2026 Luke Granger-Brown <git@lukegb.com>
# SPDX-License-Identifier: MIT

{ ... }:
{
  projectRootFile = "flake.nix";

  programs.nixfmt.enable = true;
  programs.deadnix.enable = true;

  programs.ruff-check.enable = true;
  programs.ruff-format.enable = true;

  programs.taplo.enable = true;

  programs.deno = {
    enable = true;
    excludes = [ "*.html" ]; # makes a mess of Jinja
  };

  programs.djlint.enable = true;
}
