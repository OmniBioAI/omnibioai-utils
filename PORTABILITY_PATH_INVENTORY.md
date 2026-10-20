# Path portability inventory

Active utility defaults now resolve through `ecosystem_path.sh`: `OMNIBIOAI_ROOT` takes precedence, otherwise the parent of this repository is used. Positional root arguments remain supported where they existed.

The obsolete `Desktop/omnibioai-ecosystem` references found in active utilities were replaced in the build, backup, coverage, startup, sync, sweep, audit, and data helper scripts. Historical cron examples, generated `build-results.txt`, migration notes, test fixtures, and PubMed archival documentation retain their original paths as records of past runs. Ordinary identifiers and unrelated prose were left unchanged.

Validation covers default resolution, an override containing spaces, Bash syntax, Python compilation, and `git diff --check`. No backup, restore, sync, build, deployment, or stack-start command was executed.
