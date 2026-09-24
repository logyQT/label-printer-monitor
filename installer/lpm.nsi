; NSIS installer script for lpm (Label Printer Monitor)
;
; Build:
;   1. python build.py               -> dist/main.dist/lpm.exe (standalone)
;   2. makensis installer\lpm.nsi    -> installer/lpm-setup.exe
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

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "English"

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

    ; This installer runs elevated, but the scheduled task runs
    ; LeastPrivilege: a folder created here inherits admin-only ACLs, so
    ; grant BUILTIN\Users modify explicitly. *S-1-5-32-545 is that group's
    ; locale-independent SID.
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
; Uninstall
; ---------------------------------------------------------------------------
Section "Uninstall"
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
    ; The ProgramData folder (%ProgramData%\com.logy.lpm) is intentionally
    ; left in place: removing the program must not delete collected printer
    ; history and the config.
    RMDir /r "$INSTDIR"

    DeleteRegKey HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\lpm"
    DeleteRegKey HKLM "Software\lpm"
SectionEnd
