#define MyAppName "JARVIS"
#define MyAppPublisher "Wadia"
#define MyAppExeName "JARVIS.exe"

#include "VERSION"

[Setup]
AppId={{7E1F2A44-9C3B-4B8D-8A57-1A2B3C4D5E6F}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\JARVIS
DefaultGroupName={#MyAppName}
PrivilegesRequired=lowest
DisableDirPage=no
DisableProgramGroupPage=yes
OutputDir=installer_output
OutputBaseFilename=JARVIS_setup
SetupIconFile=jarvis.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\JARVIS.exe

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: checkedonce

[Files]
Source: "dist\JARVIS\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\jarvis.ico"; Tasks: desktopicon
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "JARVIS"; ValueData: """{app}\JARVIS.exe"" --hidden"; Flags: uninsdeletevalue

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{localappdata}\JARVIS"

[Code]
procedure CurStepChanged(CurStep: TSetupStep);
var
  DataDir, Content: String;
begin
  if CurStep = ssPostInstall then
  begin
    DataDir := ExpandConstant('{localappdata}\JARVIS');
    ForceDirectories(DataDir);
    if not FileExists(DataDir + '\config.env') then
    begin
      Content := 'WAKE_ENABLED=1' + #13#10 +
                 'STT_MODEL=tiny.en' + #13#10 +
                 'STT_LANG=auto' + #13#10 +
                 'TTS_VOICE=en-US-AndrewNeural' + #13#10 +
                 'TTS_RATE=+30%' + #13#10;
      SaveStringToFile(DataDir + '\config.env', Content, False);
    end;
  end;
end;
