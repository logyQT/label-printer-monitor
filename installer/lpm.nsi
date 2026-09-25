; NSIS installer script for lpm (Label Printer Monitor)
;
; Build:
;   1. python build.py               -> dist/main.dist/lpm.exe (standalone)
;   2. makensis installer\lpm.nsi    -> installer/lpm-setup.exe
;
; Uninstall behavior - the components page doubles as the confirmation step:
;   [x] Scheduled collection task    always removed (read-only). A task left
;                                    behind would keep firing at the deleted
;                                    lpm.exe.
;   [ ] Prune configuration          opt-in: <data root>\config
;   [ ] Prune data                   opt-in: <data root>\data (database + backups)
;   [ ] Prune logs                   opt-in: <data root>\logs
;   [x] Program files, PATH, registry always removed (read-only)
;
;   Cleanup delegates to `lpm purge ...` while lpm.exe is still installed
;   (it resolves %LPM_HOME%/%ProgramData% itself and drops the data root
;   when the prunes emptied it), with schtasks/RMDir fallbacks for a broken
;   install. Silent: uninstall.exe /S (mandatory parts only) or
;   uninstall.exe /S /PURGE (everything).
;
; Requires: NSIS 3.x (https://nsis.sourceforge.io/Download)

!include "MUI2.nsh"
!include "FileFunc.nsh"

; ---------------------------------------------------------------------------
; General
; ---------------------------------------------------------------------------
Name "lpm"
OutFile "lpm-setup.exe"
InstallDir "$PROGRAMFILES\lpm"
InstallDirRegKey HKLM "Software\lpm" "InstallDir"
RequestExecutionLevel admin

; ---------------------------------------------------------------------------
; Version info
; ---------------------------------------------------------------------------
VIProductVersion "1.0.0.0"
VIAddVersionKey "ProductName" "lpm"
VIAddVersionKey "FileVersion" "1.0.0"
VIAddVersionKey "FileDescription" "Label Printer Monitor Installer"
VIAddVersionKey "LegalCopyright" "logy"

; ---------------------------------------------------------------------------
; Interface
; ---------------------------------------------------------------------------
!define MUI_ABORTWARNING
!define MUI_ICON "${NSISDIR}\Contrib\Graphics\Icons\modern-install.ico"
!define MUI_UNICON "${NSISDIR}\Contrib\Graphics\Icons\modern-uninstall.ico"

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "LICENSE.txt"
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

; Replaces MUI_UNPAGE_CONFIRM: the cleanup checkboxes ARE the confirmation -
; mandatory items are read-only, everything else is opt-in (off by default).
!insertmacro MUI_UNPAGE_COMPONENTS
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "English"

; Hover descriptions for the uninstall components page.
LangString SecTaskDesc ${LANG_ENGLISH} "The \LPM\LPM_Collect scheduled task. Always removed - it would keep firing at a deleted lpm.exe otherwise."
LangString SecPruneConfigDesc ${LANG_ENGLISH} "config.json, schema and example in <data root>\config. Kept unless checked."
LangString SecPruneDataDesc ${LANG_ENGLISH} "Statistics database and backups in <data root>\data - your collected printer history. Kept unless checked."
LangString SecPruneLogsDesc ${LANG_ENGLISH} "Log files in <data root>\logs. Kept unless checked."
LangString SecProgramDesc ${LANG_ENGLISH} "Program files, the PATH entry and the Add/Remove Programs entry."

; ---------------------------------------------------------------------------
; Install
; ---------------------------------------------------------------------------
Section "Install"
    ; Standalone build: ship everything from dist/main.dist/
    SetOutPath "$INSTDIR"
    File /r "..\dist\main.dist\*.*"

    WriteRegStr HKLM "Software\lpm" "InstallDir" "$INSTDIR"

    ; --- Add to system PATH (remove first to avoid duplicates, then append) ---
    ReadRegStr $0 HKLM \
        "SYSTEM\CurrentControlSet\Control\Session Manager\Environment" "Path"

    StrLen $1 $0
    StrLen $2 "$INSTDIR"

    ${If} $1 > 0
        ; Remove existing entry first (handles exact, start, end positions)
        StrCmp $0 "$INSTDIR" path_only_dir
        StrCpy $3 $0 $2
        StrCmp $3 "$INSTDIR" 0 path_try_end
        StrCpy $4 $0 1 $2
        StrCmp $4 ";" 0 path_try_end
        IntOp $3 $2 + 1
        StrCpy $0 $0 "" $3
        Goto path_append
        path_try_end:
        IntOp $3 $1 - $2
        StrCpy $4 $0 1 $3
        StrCmp $4 ";" 0 path_append
        StrCpy $0 $0 $3
        Goto path_append
        path_only_dir:
        StrCpy $0 ""
    ${EndIf}

    path_append:
    StrLen $1 $0
    ${If} $1 == 0
        StrCpy $0 "$INSTDIR"
    ${Else}
        StrCpy $2 $0 1 -1
        ${If} $2 == ";"
            StrCpy $0 "$0$INSTDIR"
        ${Else}
            StrCpy $0 "$0;$INSTDIR"
        ${EndIf}
    ${EndIf}
    WriteRegStr HKLM \
        "SYSTEM\CurrentControlSet\Control\Session Manager\Environment" "Path" "$0"

    WriteUninstaller "$INSTDIR\uninstall.exe"

    ; --- Machine-wide data directory -----------------------------------
    ; Config, database and logs live outside any user profile, so a headless
    ; scheduled run resolves the same paths no matter whose account
    ; registered the task (and whether that profile is loaded at all).
    ; NSIS has no built-in constant for the common appdata directory, so the
    ; variable is expanded explicitly (and falls back to the well-known path,
    ; mirroring _DEFAULT_PROGRAMDATA in src\env.py).
    ExpandEnvStrings $R9 "%ProgramData%"
    ${If} $R9 == ""
    ${OrIf} $R9 == "%ProgramData%"
        StrCpy $R9 "C:\ProgramData"
    ${EndIf}
    CreateDirectory "$R9\com.logy.lpm"

    ; This installer runs elevated, and so does the scheduled task (it runs
    ; as SYSTEM), but the interactive CLI usually does not: a folder created
    ; here inherits admin-only ACLs, so grant BUILTIN\Users modify explicitly.
    ; *S-1-5-32-545 is that group's locale-independent SID.
    ExecWait 'icacls "$R9\com.logy.lpm" /grant *S-1-5-32-545:(OI)(CI)M /Q' $0
    ${If} $0 != 0
        DetailPrint "icacls could not grant write access (exit code $0) - scheduled runs may fail to write data"
    ${EndIf}

    ; --- Add/Remove Programs entry ---
    WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\lpm" \
        "DisplayName" "lpm - Label Printer Monitor"
    WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\lpm" \
        "UninstallString" '"$INSTDIR\uninstall.exe"'
    WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\lpm" \
        "InstallLocation" "$INSTDIR"
    WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\lpm" \
        "DisplayVersion" "1.0.0"
    WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\lpm" \
        "Publisher" "logy"
    WriteRegDWORD HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\lpm" \
        "NoModify" 1
    WriteRegDWORD HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\lpm" \
        "NoRepair" 1

    ${GetSize} "$INSTDIR" "/S=0K" $0 $1 $2
    IntFmt $0 "0x%08X" $0
    WriteRegDWORD HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\lpm" \
        "EstimatedSize" "$0"

    MessageBox MB_ICONINFORMATION "Installation complete.$\r$\n$\r$\nConfig, data and logs are stored in:$R9\com.logy.lpm$\r$\n$\r$\nOpen a terminal and run: lpm init"
SectionEnd

; ---------------------------------------------------------------------------
; Uninstall - helpers
; ---------------------------------------------------------------------------

; Data root fallback used by the direct-delete branch below. Mirrors
; src\env.py: %LPM_HOME% (machine env, then the current user's) wins over
; %ProgramData%\com.logy.lpm. Only needed when lpm.exe is missing or broken -
; whenever it can run, lpm.exe itself is the authority on the data root.
Function un.ResolveDataRoot
    ReadRegStr $R8 HKLM \
        "SYSTEM\CurrentControlSet\Control\Session Manager\Environment" "LPM_HOME"
    ${If} $R8 == ""
        ReadRegStr $R8 HKCU "Environment" "LPM_HOME"
    ${EndIf}
    ${If} $R8 == ""
        ExpandEnvStrings $R8 "%ProgramData%"
        ${If} $R8 == ""
        ${OrIf} $R8 == "%ProgramData%"
            StrCpy $R8 "C:\ProgramData"
        ${EndIf}
        StrCpy $R8 "$R8\com.logy.lpm"
    ${EndIf}
FunctionEnd

; One optional data prune. Prefer `lpm purge --<flag>` while lpm.exe is still
; installed (it resolves %LPM_HOME%/%ProgramData% itself and drops the data
; root when the prunes emptied it); fall back to a direct delete when the exe
; is missing or the command fails. nsExec returns "error"/"timeout" instead
; of an exit code - comparisons against "0" are string compares, so those
; land in the fallback branch as intended.
!macro PruneDataDir FLAG
    DetailPrint "Pruning ${FLAG}..."
    nsExec::ExecToLog '"$INSTDIR\lpm.exe" purge --${FLAG}'
    Pop $R7
    ${If} $R7 == "0"
        DetailPrint "lpm purge --${FLAG}: done"
    ${Else}
        DetailPrint "lpm purge --${FLAG} unavailable (result: $R7) - deleting directly"
        Call un.ResolveDataRoot
        DetailPrint "Removing $R8\${FLAG}"
        RMDir /r "$R8\${FLAG}"
        ; Drop the data root when that emptied it - non-recursive RMDir
        ; fails harmlessly while anything else remains in it.
        RMDir "$R8"
    ${EndIf}
!macroend

; ---------------------------------------------------------------------------
; Uninstall - sections
; ---------------------------------------------------------------------------
; Sections run in declaration order while $INSTDIR\lpm.exe is still on disk,
; so all cleanup comes before the program files (SecProgram, last).
; Each name needs the 'un.' prefix to be classified as an uninstaller
; section; un.onInit below rewrites the display names so the components tree
; shows clean labels whatever the compiler does with the prefix.

Section "un.Scheduled collection task" SecTask
    SectionIn RO

    ; A collect run may still hold lpm.exe and the database - end it first.
    ; Harmless when the task is not running or not registered.
    nsExec::ExecToLog '"$SYSDIR\schtasks.exe" /End /TN "\LPM\LPM_Collect" /Q'
    Pop $R7

    ; lpm purge also removes the empty \LPM folder, which schtasks cannot do.
    ; nsExec yields "error" when lpm.exe itself is missing.
    nsExec::ExecToLog '"$INSTDIR\lpm.exe" purge --tasks'
    Pop $R7
    ${If} $R7 != "0"
        DetailPrint "lpm purge --tasks unavailable (result: $R7) - falling back to schtasks"
        nsExec::ExecToLog '"$SYSDIR\schtasks.exe" /Delete /TN "\LPM\LPM_Collect" /F'
        Pop $R7
        DetailPrint "schtasks /Delete result: $R7 (1 = no such task)"
    ${EndIf}

    ; Verify: a task left behind would keep firing at the deleted lpm.exe.
    nsExec::ExecToStack '"$SYSDIR\schtasks.exe" /Query /TN "\LPM\LPM_Collect"'
    Pop $R7
    Pop $R8
    ${If} $R7 == "0"
        DetailPrint "WARNING: \LPM\LPM_Collect still exists - remove it manually with:"
        DetailPrint "  schtasks /Delete /TN \LPM\LPM_Collect /F"
    ${EndIf}
SectionEnd

Section /o "un.Prune configuration" SecPruneConfig
    !insertmacro PruneDataDir config
SectionEnd

Section /o "un.Prune data" SecPruneData
    !insertmacro PruneDataDir data
SectionEnd

Section /o "un.Prune logs" SecPruneLogs
    !insertmacro PruneDataDir logs
SectionEnd

Section "un.Program files, PATH and registry" SecProgram
    SectionIn RO

    ; --- Remove from system PATH ---
    ReadRegStr $0 HKLM \
        "SYSTEM\CurrentControlSet\Control\Session Manager\Environment" "Path"

    StrLen $1 $0
    StrLen $2 "$INSTDIR"

    ${If} $1 > 0
        ; Exact match (PATH is only the install dir)
        StrCmp $0 "$INSTDIR" remove_clear

        ; Starts with "DIR;"
        StrCpy $3 $0 $2
        StrCmp $3 "$INSTDIR" 0 remove_try_end
        StrCpy $4 $0 1 $2
        StrCmp $4 ";" 0 remove_try_end
        IntOp $3 $2 + 1
        StrCpy $0 $0 "" $3
        Goto remove_write

        remove_try_end:
        ; Ends with ";DIR"
        IntOp $3 $1 - $2
        StrCpy $4 $0 1 $3
        StrCmp $4 ";" 0 remove_write
        StrCpy $0 $0 $3
        Goto remove_write

        remove_clear:
        StrCpy $0 ""

        remove_write:
        WriteRegStr HKLM \
            "SYSTEM\CurrentControlSet\Control\Session Manager\Environment" "Path" "$0"
    ${EndIf}

    ; --- Delete all installed files ---
    ; Runs last: the cleanup sections above need lpm.exe. The data root
    ; (%ProgramData%\com.logy.lpm) survives unless the prune boxes were
    ; checked - removing the program must not silently delete collected
    ; printer history and the config.
    RMDir /r "$INSTDIR"

    DeleteRegKey HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\lpm"
    DeleteRegKey HKLM "Software\lpm"
SectionEnd

; ---------------------------------------------------------------------------
; Uninstall - startup
; ---------------------------------------------------------------------------
; Declared below the sections so ${Sec...} IDs resolve (compile-time order).
Function un.onInit
    ; Display names without the 'un.' prefix the compiler needs to classify
    ; the sections above as uninstaller sections (set before any page shows).
    SectionSetText ${SecTask} "Scheduled collection task"
    SectionSetText ${SecPruneConfig} "Prune configuration"
    SectionSetText ${SecPruneData} "Prune data"
    SectionSetText ${SecPruneLogs} "Prune logs"
    SectionSetText ${SecProgram} "Program files, PATH and registry"

    ; uninstall.exe [/S] [/PURGE] - /PURGE pre-checks every optional prune
    ; box. It is also the only way to get them in a silent uninstall: pages
    ; are skipped there, so the boxes would keep their unchecked defaults.
    ClearErrors
    ${GetParameters} $R0
    ${GetOptions} "$R0" "/PURGE" $R1
    IfErrors purge_flag_done
    SectionSetFlags ${SecPruneConfig} ${SF_SELECTED}
    SectionSetFlags ${SecPruneData} ${SF_SELECTED}
    SectionSetFlags ${SecPruneLogs} ${SF_SELECTED}
    purge_flag_done:
FunctionEnd

; Uninstall components page descriptions (hover text).
!insertmacro MUI_UNFUNCTION_DESCRIPTION_BEGIN
    !insertmacro MUI_DESCRIPTION_TEXT ${SecTask} $(SecTaskDesc)
    !insertmacro MUI_DESCRIPTION_TEXT ${SecPruneConfig} $(SecPruneConfigDesc)
    !insertmacro MUI_DESCRIPTION_TEXT ${SecPruneData} $(SecPruneDataDesc)
    !insertmacro MUI_DESCRIPTION_TEXT ${SecPruneLogs} $(SecPruneLogsDesc)
    !insertmacro MUI_DESCRIPTION_TEXT ${SecProgram} $(SecProgramDesc)
!insertmacro MUI_UNFUNCTION_DESCRIPTION_END
