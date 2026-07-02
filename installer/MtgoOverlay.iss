; Inno Setup script for MTGO Draft Helper.
; Build the app first (.\build.ps1 -> dist\MtgoOverlay\), then compile:
;   ISCC.exe /DAppVersion=0.2.0 installer\MtgoOverlay.iss
; The version is passed in by CI from src\mtgo_overlay\__init__.py's __version__;
; the default below only exists so a manual local compile doesn't fail.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

#define AppName "MTGO Draft Helper"
#define AppPublisher "g0dnerd"
#define AppExeName "MtgoOverlay.exe"
#define AppURL "https://github.com/g0dnerd/mtgo_overlay"

[Setup]
; A stable AppId keeps upgrades/uninstalls tied to the same install across versions.
AppId={{6F3A2C1E-7B4D-4E9A-9C2F-1D8E5A3B7C40}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
AppUpdatesURL={#AppURL}/releases
; Per-user install: no admin/UAC prompt, friendlier for a non-technical friend.
PrivilegesRequired=lowest
DefaultDirName={localappdata}\Programs\MtgoOverlay
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\{#AppExeName}
UninstallDisplayName={#AppName}
; Resolve [Files] Source and OutputDir from the repo root (parent of installer\),
; not the script's own directory. Relative to the .iss file, so it holds for both
; a local run and CI regardless of the working directory.
SourceDir=..
OutputDir=dist
OutputBaseFilename=MtgoOverlaySetup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; In-place upgrade safety net: if the app is still running (e.g. it launched this
; installer and is mid-exit), close it before overwriting its files, and don't
; try to restart it - the user relaunches from the tray/Start menu.
CloseApplications=yes
RestartApplications=no
; The onedir bundle is 64-bit; only offer install on 64-bit Windows.
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "startmenu"; Description: "Create a Start menu shortcut"; GroupDescription: "Shortcuts:"
Name: "startup"; Description: "Start {#AppName} when I sign in to Windows"; GroupDescription: "Startup:"; Flags: unchecked

[Files]
; The whole PyInstaller --onedir output. recursesubdirs pulls in _internal\.
Source: "dist\MtgoOverlay\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: startmenu
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"; Tasks: startmenu

[Registry]
; Run-on-sign-in under HKCU (matches the per-user install scope).
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; \
    ValueType: string; ValueName: "MtgoOverlay"; ValueData: """{app}\{#AppExeName}"""; \
    Flags: uninsdeletevalue; Tasks: startup

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Launch {#AppName} now"; \
    Flags: nowait postinstall skipifsilent
