; The installer's "Include the offline AI" page (backlog A12).  Ticked, the
; installer leaves a note in the desk's settings folder; on its first start
; the desk downloads the offline AI model (about 5 GB) in the background, so
; the copilot works without internet once it is done - nothing else to do.
; Unticked, or a silent install: Settings -> AI offers it later.
!include nsDialogs.nsh
!include LogicLib.nsh

!ifndef BUILD_UNINSTALLER
Var AiBox
Var AiWant

!macro customPageAfterChangeDir
  Page custom JarvisAiPage JarvisAiPageLeave
!macroend

Function JarvisAiPage
  ; (MUI's header macro isn't defined yet where electron-builder includes
  ; this file, so the page says it all in its own text)
  nsDialogs::Create 1018
  Pop $0
  ${If} $0 == error
    Abort
  ${EndIf}
  ${NSD_CreateLabel} 0 0 100% 72u "Offline AI - the copilot on this computer, with no internet and no daily limit.$\r$\n$\r$\nJarvis can download its offline AI (about 5 GB) in the background the first time it starts. Once it is done, the copilot works at a venue with no internet. It needs 16 GB of memory. You can also add it later in Settings -> AI."
  Pop $0
  ${NSD_CreateCheckbox} 0 80u 100% 12u "Include the offline AI (about 5 GB download)"
  Pop $AiBox
  ${If} $AiWant == ""
    StrCpy $AiWant ${BST_CHECKED}
  ${EndIf}
  ${NSD_SetState} $AiBox $AiWant
  nsDialogs::Show
FunctionEnd

Function JarvisAiPageLeave
  ${NSD_GetState} $AiBox $AiWant
FunctionEnd

!macro customInstall
  ${If} $AiWant == ${BST_CHECKED}
    CreateDirectory "$APPDATA\${PRODUCT_NAME}"
    FileOpen $0 "$APPDATA\${PRODUCT_NAME}\offline-ai-wanted" w
    FileWrite $0 "yes"
    FileClose $0
  ${EndIf}
!macroend
!endif
