{
  # The toolchain host-mode Forge sessions share through Nix, as one package set
  # that installs the same way on a session VM, a plain Linux host or a Mac:
  #
  #   nix profile install github:niuulabs/niuu?dir=deploy/nix#baseline
  #
  # A VM's machine profile installs it into the default profile (install-nix.sh,
  # NIX_BASELINE) so every slot has it on PATH; sessions add their own packages on
  # top with `nix profile install nixpkgs#<package>`.
  description = "Niuu host-mode baseline toolchain";

  # nixos-26.05 on 2026-11-05, by revision: resolving a branch name calls the
  # GitHub API, which rate-limits a shared egress address.
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/c56a87d8e41ce69bd2af57eb35dcc117a9dfca33";

  outputs =
    { self, nixpkgs }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
        "x86_64-darwin"
        "aarch64-darwin"
      ];
      forAll = f: nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});
    in
    {
      packages = forAll (pkgs: {
        baseline = pkgs.buildEnv {
          name = "niuu-host-baseline";
          paths = with pkgs; [
            coreutils
            curl
            git
            gnumake
            go
            jq
            nodejs_22
            pnpm
            python3
            ripgrep
            stdenv.cc
            uv
          ];
        };
        default = self.packages.${pkgs.stdenv.hostPlatform.system}.baseline;
      });
    };
}
