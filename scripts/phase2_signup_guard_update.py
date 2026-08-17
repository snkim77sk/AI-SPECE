from pathlib import Path

path = Path('.github/workflows/signup-approval-smoke.yml')
text = path.read_text(encoding='utf-8')
old = '''          BASELINE=7bdeabf2c04e18ce0b72775849e06d486376615f
          git diff --exit-code "$BASELINE" -- collector_v200.py scheduler.py db.py g2b_sync.py sinsung_runtime_fix.py sinsung_v220_ui.py sinsung_v220_app.py
          changed=$(git diff --name-only "$BASELINE" HEAD)
          unexpected=$(printf '%s\\n' "$changed" | grep -vE '^(main\\.py|sinsung_signup_approval\\.py|sinsung_budget_item_mapping\\.py|\\.github/workflows/signup-approval-smoke\\.yml)$' || true)
          test -z "$unexpected"
          echo CORE_22_UNCHANGED_OK'''
new = '''          BASELINE=7bdeabf2c04e18ce0b72775849e06d486376615f
          if [[ "$GITHUB_REF_NAME" == refactor/phase2-* ]]; then
            # Phase 2 intentionally centralizes version ownership. The data engine,
            # scheduler, DB and API-key behavior must remain byte-for-byte Phase 1.
            git diff --exit-code "$BASELINE" -- collector_v200.py scheduler.py db.py g2b_sync.py
            changed=$(git diff --name-only "$BASELINE" HEAD)
            unexpected=$(printf '%s\\n' "$changed" | grep -vE '^(app_version\\.py|app\\.py|server\\.py|main\\.py|sinsung_runtime_fix\\.py|sinsung_ui_restore\\.py|sinsung_region_fix\\.py|sinsung_budget_monitor\\.py|sinsung_v251_patch\\.py|sinsung_v252_patch\\.py|sinsung_v220_ui\\.py|sinsung_v220_app\\.py|sinsung_signup_approval\\.py|sinsung_budget_item_mapping\\.py|\\.github/workflows/signup-approval-smoke\\.yml|\\.github/workflows/phase2-version-render\\.yml)$' || true)
            test -z "$unexpected"
            echo CORE_22_BEHAVIOR_PRESERVED_DURING_PHASE2_OK
          else
            git diff --exit-code "$BASELINE" -- collector_v200.py scheduler.py db.py g2b_sync.py sinsung_runtime_fix.py sinsung_v220_ui.py sinsung_v220_app.py
            changed=$(git diff --name-only "$BASELINE" HEAD)
            unexpected=$(printf '%s\\n' "$changed" | grep -vE '^(main\\.py|sinsung_signup_approval\\.py|sinsung_budget_item_mapping\\.py|\\.github/workflows/signup-approval-smoke\\.yml)$' || true)
            test -z "$unexpected"
            echo CORE_22_UNCHANGED_OK
          fi'''
if text.count(old) != 1:
    raise SystemExit(f'expected one baseline block, found {text.count(old)}')
path.write_text(text.replace(old, new, 1), encoding='utf-8')
print('PHASE2_SIGNUP_GUARD_UPDATED')
