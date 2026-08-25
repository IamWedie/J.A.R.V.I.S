#define MyAppName "JARVIS"
#define MyAppVersion "1.0"
#define MyAppPublisher "Wadia"
#define MyAppExeName "JARVIS.exe"

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
Name: "autostart"; Description: "Start JARVIS automatically when I log in"
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: checkedonce

[Files]
Source: "dist\JARVIS\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\jarvis.ico"; Tasks: desktopicon
Name: "{userstartup}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\jarvis.ico"; Tasks: autostart
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{localappdata}\JARVIS"

[Code]
var
  KeyPage: TInputQueryWizardPage;

procedure InitializeWizard;
begin
  KeyPage := CreateInputQueryPage(wpSelectDir,
    'Connect JARVIS to your AI brain',
    'Enter your OpenCode Zen API key',
    'Create a free key at opencode.ai/auth, then paste it below. Your key is stored only on this PC.' + #13#10 +
    'Leave blank to set this up inside the app later.');
  KeyPage.Add('Zen API key (sk-...)', True);
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := False;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  DataDir, Key: String;
begin
  if CurStep = ssPostInstall then
  begin
    Key := Trim(KeyPage.Edits[0].Text);
    if Key <> '' then
    begin
      DataDir := ExpandConstant('{localappdata}\JARVIS');
      ForceDirectories(DataDir);
      SaveStringToFile(DataDir + '\config.env',
        'ZEN_API_KEY=' + Key + #13#10 +
        'ZEN_MODEL=mimo-v2.5-free' + #13#10,
        False);
    end;
  end;
end;
