; NSIS hooks for the Merc Wizard installer.
;
; Tauri's NSIS template calls `NSIS_HOOK_POSTUNINSTALL` after the standard
; uninstall steps. We use it to optionally clear the per-user data folder
; (%APPDATA%\MercWizard) which the default Tauri uninstaller leaves behind
; because it only cleans $APPDATA\<bundle-id>.
;
; In silent mode (typical for installer-driven upgrade), the user's data
; is preserved — the prompt would block automation and most upgrades
; don't want to wipe user data anyway.

!macro NSIS_HOOK_POSTUNINSTALL
    ${If} ${Silent}
        ; Silent uninstall (likely an upgrade) — keep user data.
    ${Else}
        MessageBox MB_YESNO|MB_ICONQUESTION "Also delete Merc Wizard's data folder?$\r$\n$\r$\nThis is %APPDATA%\MercWizard and contains your backups, logs, and settings.$\r$\nThis action cannot be undone." IDNO mw_keep_user_data
        RMDir /r "$APPDATA\MercWizard"
        mw_keep_user_data:
    ${EndIf}
!macroend
