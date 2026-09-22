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
; VIAddVersionKey "LegalCopyright" "logy"

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
; PATH manipulation using pure built-in NSIS string functions
;
; Reads the system PATH from the registry, walks segments separated
; by ";", and adds or removes the install directory.
; ---------------------------------------------------------------------------

!macro AddToPath DIR
    ReadRegStr $0 HKLM \
        "SYSTEM\CurrentControlSet\Control\Session Manager\Environment" "Path"

    StrLen $1 $0
    StrCmp $1 0 0 +3
        WriteRegStr HKLM \
            "SYSTEM\CurrentControlSet\Control\Session Manager\Environment" "Path" "${DIR}"
        Goto add_done

    ; Walk segments, skip any that already match DIR
    StrCpy $2 ""    ; rebuilt PATH
    StrCpy $3 ""    ; current segment
    StrCpy $4 0     ; position

    add_loop:
        StrCpy $5 $0 1 $4
        StrCmp $5 ";" add_seg_end
        StrCmp $5 "" add_seg_end
        StrCpy $3 "$3$5"
        IntOp $4 $4 + 1
        Goto add_loop

    add_seg_end:
        ; Compare segment to DIR (case-insensitive)
        StrCmpS $3 "${DIR}" add_skip
        StrLen $5 $2
        StrCmp $5 0 add_first_seg
        StrCpy $5 $2 1 -1
        StrCmp $5 ";" add_append_no_semi
        StrCpy $2 "$2;$3"
        Goto add_continue
        add_append_no_semi:
        StrCpy $2 "$2$3"
        Goto add_continue

        add_first_seg:
        StrCpy $2 "$3"

        add_continue:
        StrCpy $3 ""
        StrCmp $5 "" add_done
        IntOp $4 $4 + 1
        Goto add_loop

        add_skip:
        StrCpy $3 ""
        StrCmp $5 "" add_done
        IntOp $4 $4 + 1
        Goto add_loop

    add_done:
        WriteRegStr HKLM \
            "SYSTEM\CurrentControlSet\Control\Session Manager\Environment" "Path" "$2"
!macroend

!macro RemoveFromPath DIR
    ReadRegStr $0 HKLM \
        "SYSTEM\CurrentControlSet\Control\Session Manager\Environment" "Path"

    StrLen $1 $0
    StrCmp $1 0 remove_done

    StrCpy $2 ""    ; rebuilt PATH
    StrCpy $3 ""    ; current segment
    StrCpy $4 0     ; position

    remove_loop:
        StrCpy $5 $0 1 $4
        StrCmp $5 ";" remove_seg_end
        StrCmp $5 "" remove_seg_end
        StrCpy $3 "$3$5"
        IntOp $4 $4 + 1
        Goto remove_loop

    remove_seg_end:
        StrCmpS $3 "${DIR}" remove_skip
        StrLen $5 $2
        StrCmp $5 0 remove_first_seg
        StrCpy $5 $2 1 -1
        StrCmp $5 ";" remove_append_no_semi
        StrCpy $2 "$2;$3"
        Goto remove_continue
        remove_append_no_semi:
        StrCpy $2 "$2$3"
        Goto remove_continue

        remove_first_seg:
        StrCpy $2 "$3"

        remove_continue:
        StrCpy $3 ""
        StrCmp $5 "" remove_done
        IntOp $4 $4 + 1
        Goto remove_loop

        remove_skip:
        StrCpy $3 ""
        StrCmp $5 "" remove_done
        IntOp $4 $4 + 1
        Goto remove_loop

    remove_done:
        WriteRegStr HKLM \
            "SYSTEM\CurrentControlSet\Control\Session Manager\Environment" "Path" "$2"
!macroend

; ---------------------------------------------------------------------------
; Install
; ---------------------------------------------------------------------------
Section "Install"
    SetOutPath "$INSTDIR"

    File "..\dist\lpm.exe"

    WriteRegStr HKLM "Software\lpm" "InstallDir" "$INSTDIR"

    !insertmacro AddToPath "$INSTDIR"

    WriteUninstaller "$INSTDIR\uninstall.exe"

    ; Add/Remove Programs entry
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
    !insertmacro RemoveFromPath "$INSTDIR"

    Delete "$INSTDIR\lpm.exe"
    Delete "$INSTDIR\uninstall.exe"
    RMDir  "$INSTDIR"

    DeleteRegKey HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\lpm"
    DeleteRegKey HKLM "Software\lpm"
SectionEnd
