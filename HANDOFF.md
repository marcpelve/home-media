# Project Handoff: home-media

**Date:** 2026-11-11 20:45
**Branch:** main
**Latest commit:** 2104c47 Update STATE.md: GitHub release pipeline status

## Done
- P0–P7 complete: backend (FastAPI + SQLite + TMDB + Torbox + mpv + Trakt), Tauri desktop shell, frontend (home, search, detail, source picker, now-playing, library, settings), packaging (PyInstaller one-dir + NSIS/MSI installers)
- 130/130 backend tests passing
- v0.1.0 release live on GitHub with NSIS installer
- GitHub repo pushed (`marcpelve/home-media`), CI pipeline working
- Security audit: clean (no Megalodon indicators)
- Workflow fixes: pip install, resources dir, tagName, release creation, asset upload

## Current state
- **Working:** v0.1.0 installer downloadable, CI builds successfully, all app features functional
- **In-flight:** v0.1.1 tag pushed, CI stuck in `queued` (runs 43095230132, 43096541945) — GitHub runner pool congestion
- **Missing from release:** updater artifacts (.sig, .nsis.zip, .nsis.zip.sig), updater manifest (latest.json) — tauri-action `tagName` fix is in place but untested
- **Pending:** clean-machine installer verification (user needs to run on a fresh Windows box)

## Blockers
- GitHub Actions `windows-latest` runner pool congested — both v0.1.1 runs uncancelable via API. Must wait for GitHub to clear the queue or time out the runs.

## Next steps
1. Check if v0.1.1 CI runs completed or timed out (`gh run list`)
2. If timed out: `git push origin :refs/tags/v0.1.1` then re-tag and push
3. Verify v0.1.1 release has all 4 assets + updater manifest
4. Test in-app updater: install v0.1.0 → check for update → v0.1.1
5. Clean-machine installer run (user): fresh Windows box, run NSIS, verify app boots + backend binds :8790
6. Consider parameterizing version in workflow (derive from tag, not hardcoded)

## Key decisions
- `gh release upload` over raw curl for asset upload (handles auth token format + filename encoding)
- Tag derived from `GITHUB_REF` not `github.tag_name` (latter empty on re-pushed tags)
- `tagName: ${{ github.ref_name }}` on tauri-action — required for updater artifact generation
- App data (`com.homemedia.app`) separate from install location — survives reinstalls
- Action refs use tags not pinned SHAs — user deferred pinning ("fine for now")
- No autonomous commit/PR rules apply to this repo (that's llama.cpp-specific)

## References
- Repo: `C:\ai\repos\home-media`
- GitHub: `https://github.com/marcpelve/home-media`
- v0.1.0 release: `https://github.com/marcpelve/home-media/releases/tag/v0.1.0`
- Workflow: `.github/workflows/release.yml`
- STATE.md: full chronological project log
- App data: `C:\Users\marcp\AppData\Local\com.homemedia.app`
- Old install: `C:\Users\marcp\AppData\Local\Home Media\uninstall.exe`
- Stuck CI runs: 43095230132, 43096541945
