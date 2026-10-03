import pathlib


REPO = pathlib.Path(__file__).resolve().parents[1]
CMD = REPO / "RUN_FULL_COLLECTION.cmd"
PS1 = REPO / "scripts" / "run_full_collection.ps1"


def test_full_collection_launcher_files_exist():
    assert CMD.is_file()
    assert PS1.is_file()


def test_cmd_invokes_powershell_wrapper_without_secret_argument():
    text = CMD.read_text(encoding="utf-8")
    assert "scripts\\run_full_collection.ps1" in text
    assert "--g2b-key" not in text
    assert "G2B_SERVICE_KEY" not in text


def test_powershell_uses_parent_g2b_paths_and_dpapi_key_file():
    text = PS1.read_text(encoding="utf-8")
    assert '$G2BRoot = Split-Path -Parent $ProgramRoot' in text
    assert 'data\\g2b-local.sqlite3' in text
    assert 'snapshot\\result-snapshot.json.gz' in text
    assert 'g2b-service-key.dpapi' in text


def test_powershell_passes_key_only_via_process_environment():
    text = PS1.read_text(encoding="utf-8")
    assert '$env:G2B_SERVICE_KEY = $ServiceKey' in text
    assert '$env:G2B_SERVICE_KEY = $null' in text
    assert "--g2b-key" not in text


def test_full_collection_uses_forward_start_and_dynamic_d_minus_one():
    text = PS1.read_text(encoding="utf-8")
    assert '--start-date "2026-09-01"' in text
    assert "--max-days 31" in text
    assert "--progress" in text
    assert "--end-date" not in text


def test_launcher_accepts_current_4_1_patch_releases():
    text = PS1.read_text(encoding="utf-8")
    assert "G2B_PROGRAM_VERSION_4_1_X_REQUIRED" in text
    assert '4\\.1\\.\\d+' in text
    assert "G2B_PROGRAM_VERSION_4_1_0_REQUIRED" not in text
    assert "G2B_PROGRAM_VERSION_4_0_0_REQUIRED" not in text

def test_launcher_does_not_delete_or_vacuum_source_storage():
    text = (CMD.read_text(encoding="utf-8") + "\n" + PS1.read_text(encoding="utf-8")).lower()
    forbidden = ["vacuum", "remove-item", "del /", "drop table", "delete from raw_records"]
    assert not any(token in text for token in forbidden)


def test_launcher_handles_dpapi_without_echoing_decrypted_value():
    text = PS1.read_text(encoding="utf-8")
    assert "ProtectedData]::Unprotect" in text
    assert "DataProtectionScope]::CurrentUser" in text
    assert "Write-Host $ServiceKey" not in text
    assert "Write-Output $ServiceKey" not in text
    assert "echo $ServiceKey" not in text.lower()


def test_launcher_writes_operational_log_outside_program_folder():
    text = PS1.read_text(encoding="utf-8")
    assert '$LogDir = Join-Path $G2BRoot "logs"' in text
    assert "Tee-Object -FilePath $LogPath" in text
    assert "Local compatibility SQLite remains unchanged" in text
    assert "checkpoint" in text


def test_launcher_user_messages_do_not_describe_production_raw_storage():
    text = (
        CMD.read_text(encoding="utf-8")
        + "\n"
        + PS1.read_text(encoding="utf-8")
    )
    assert "Existing DB/RAW" not in text
    assert "Existing RAW" not in text
    assert "Local compatibility SQLite" in text
