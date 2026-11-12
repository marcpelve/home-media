# Project Handoff: home-media

**Date:** 2026-11-12 21:05
**Branch:** main
**Latest commit:** d11996f Sign NSIS bundles in workflow (tauri build does not sign)

## Done
- Fixed the fresh-install crash: declared the vendored cfscrape runtime deps (`requests`, `urllib3`, `requests_toolbelt`, `pyparsing`, `pyaes`, `brotli`) in `backend/requirements.txt` (commit `57ad945`). A clean venv + PyInstaller build now resolves all imports (verified `urllib3` and `cfscrape` import OK).
- Bumped app version to `0.1.2` in `tauri.conf.json` + `Cargo.toml` (commit `df256e1`).
- Reworked `.github/workflows/release.yml` so the GitHub release carries all 5 assets: tauri-action creates the release + uploads the `.exe`; a follow-up step signs the NSIS bundles (creates `.nsis.zip` + `tauri sign`) and uploads `.exe.sig`, `.nsis.zip`, `.nsis.zip.sig`, and a generated `latest.json` (commits `2e2bba9`, `9e813f3`, `d11996f`).
- Release `v0.1.2` is live on GitHub with the installer `Home.Media_0.1.2_x64-setup.exe` (testable now).

## Current state
- `main` clean at `d11996f`, pushed. Tag `v0.1.2` on origin at `d11996f`.
- Run `43138843131` (sign-bundles fix) is in-flight; the 4 updater assets (`.exe.sig`, `.nsis.zip`, `.nsis.zip.sig`, `latest.json`) are not yet on the release.
- Fresh install of the `.exe` works (dep fix included). Auto-updater is inert until the 4 updater assets land on the release.

## Blockers
- None hard. The in-flight run `43138843131` is the open item. If it fails, inspect the "Sign and upload updater assets" step (it logs a debug `ls` before/after signing).

## Next steps
1. Check `gh run view 43138843131 --json status,conclusion`.
2. On success: confirm `gh release view v0.1.2 --json assets` shows all 5 files.
3. On failure: `gh run view 43138843131 --log-failed` — likely `tauri sign sign` (key/password, `tauri` CLI, or `zip`).
4. User will land real app changes before v0.1.3; do not re-push v0.1.2 to re-test.

## Key decisions
- `tauri build` does NOT sign the NSIS bundles in this setup (produces only the `.exe` in every run). The workflow signs them manually (commit `d11996f`) — do not rely on tauri-action/`tauri build` signing.
- `tauri-action@v0` uploads only the main `.exe`; its valid inputs are `projectPath`/`tagName`/`releaseName`/`includeUpdaterJson` (the dev-README names `uploadUpdaterJson`/`uploadUpdaterSignatures` are silently ignored by v0).
- The `.nsis.zip` upload asset is dot-named (`Home.Media_…`) to match the `latest.json` URL, while the on-disk bundle files use a space (`Home Media_…`).
- No self-hosted runner (user declined). The two stuck v0.1.1 runs (`43096541945`, `43095230132`) are harmless (no `concurrency` block).
- No autonomous commit/PR rules apply to this repo (that's llama.cpp-specific); direct commits to `main` are fine for small, fully-diagnosed fixes.

## References
- Repo: `C:\ai\repos\home-media`
- GitHub: `https://github.com/marcpelve/home-media`
- Release: `https://github.com/marcpelve/home-media/releases/tag/v0.1.2`
- Workflow: `.github/workflows/release.yml`
- Deps: `backend/requirements.txt`
- Version files: `desktop/src-tauri/tauri.conf.json`, `desktop/src-tauri/Cargo.toml`
- Bundle output: `desktop/src-tauri/target/release/bundle/nsis/`
- Run IDs — in-flight `43138843131`; failed `43135921275`, `43137468384`; success(partial) `43134211623`; stuck v0.1.1 `43096541945`, `43095230132`
- Commits this session: `57ad945`, `d06db76`, `df256e1`, `2e2bba9`, `9e813f3`, `d11996f`
