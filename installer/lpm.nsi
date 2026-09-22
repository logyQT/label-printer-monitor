; NSIS installer script for lpm (Label Printer Monitor)
;
; Build:
;   1. python build.py               -> dist/lpm.exe
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
    SetOutPath "$INSTDIR"
    File "..\dist\lpm.exe"

    WriteRegStr HKLM "Software\lpm" "InstallDir" "$INSTDIR"

    ; --- Add to system PATH (simple append) ---
    ReadRegStr $0 HKLM \
        "SYSTEM\CurrentControlSet\Control\Session Manager\Environment" "Path"

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

    MessageBox MB_ICONINFORMATION "Installation complete.$\r$\n$\r$\nOpen a terminal and run: lpm --init"
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

    ; --- Delete files ---
    Delete "$INSTDIR\lpm.exe"
    Delete "$INSTDIR\uninstall.exe"
    RMDir  "$INSTDIR"

    DeleteRegKey HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\lpm"
    DeleteRegKey HKLM "Software\lpm"
SectionEnd
