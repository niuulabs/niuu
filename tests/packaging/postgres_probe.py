"""Run from a Nuitka onefile build to verify the real bundled PostgreSQL tree."""

import subprocess
import tempfile
from pathlib import Path

import niuu


def main():
    binaries = Path(niuu.__file__).parent / "pginstall/bin"
    with tempfile.TemporaryDirectory(prefix="niuu-packaged-pg-") as directory:
        data = Path(directory) / "data"
        subprocess.run([binaries / "initdb", "-D", data, "--auth=trust"], check=True)
        # Unix socket only: no shared TCP port and no network listener.
        subprocess.run(
            [
                binaries / "pg_ctl",
                "-D",
                data,
                "-l",
                Path(directory) / "postgres.log",
                "-o",
                f"-k {directory} -c listen_addresses=''",
                "-w",
                "start",
            ],
            check=True,
        )
        try:
            result = subprocess.check_output(
                [
                    binaries / "psql",
                    "-h",
                    directory,
                    "-d",
                    "postgres",
                    "-Atc",
                    "SELECT 6 * 7; SELECT to_tsvector('english', 'packaging tests') "
                    "@@ plainto_tsquery('english', 'test');",
                ],
                text=True,
            )
            assert result.splitlines() == ["42", "t"], result
            print(
                "PASS: packaged initdb, postgres, pg_ctl, psql, SQL and dynamic text-search module"
            )
        finally:
            subprocess.run(
                [binaries / "pg_ctl", "-D", data, "-m", "fast", "-w", "stop"], check=True
            )


if __name__ == "__main__":
    main()
