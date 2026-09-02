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
var
  KeyPage: TInputQueryWizardPage;
  LicPage: TInputQueryWizardPage;

const
  WeakPins =
    '000000,111111,222222,333333,444444,555555,666666,777777,888888,999999,' +
    '123456,654321,112233,0910,1234,4321,9876,2580,1004,1212,1122,6969,' +
    '1590,654321,0101,0011,1010,12345,54321';

function IsWeakPin(const Pin: String): Boolean;
var
  I: Integer;
begin
  Result := False;
  for I := 0 to GetArrayLength(WeakPins) - 1 do
    if Pin = WeakPins[I] then
    begin
      Result := True;
      Exit;
    end;
end;

procedure InitializeWizard;
begin
  KeyPage := CreateInputQueryPage(wpSelectDir,
    'Connect JARVIS to your AI brain',
    'Enter your OpenCode Zen API key and set a security PIN',
    'Create a free Zen key at opencode.ai/auth, then paste it below. Set a strong ' +
    'approval PIN (at least 6 characters, not a common default). Values are stored ' +
    'only on this PC.' + #13#10 +
    'Leave the Zen key blank to set it up inside the app later.');
  KeyPage.Add('Zen API key (sk-...)', False);
  KeyPage.Add('Approval PIN (min 6 characters)', False);

  LicPage := CreateInputQueryPage(KeyPage.ID,
    'Activate JARVIS',
    'Enter your license key',
    'You received a license key when you purchased JARVIS. Paste it below to activate ' +
    'on this PC.' + #13#10 +
    'Leave blank to activate later inside the app.');
  LicPage.Add('License key (JARV-...)', False);
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  Pin: String;
begin
  Result := True;
  if CurPageID = KeyPage.ID then
  begin
    Pin := Trim(KeyPage.Edits[1].Text);
    if (Length(Pin) > 0) and ((Length(Pin) < 6) or IsWeakPin(Pin)) then
    begin
      MsgBox('The approval PIN must be at least 6 characters and should not be a ' +
        'common default. Please choose a stronger PIN.', mbError, MB_OK);
      Result := False;
    end;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  DataDir, ZenKey, Pin, LicKey, Content: String;
begin
  if CurStep = ssPostInstall then
  begin
    ZenKey := Trim(KeyPage.Edits[0].Text);
    Pin := Trim(KeyPage.Edits[1].Text);
    LicKey := Trim(LicPage.Edits[0].Text);
    DataDir := ExpandConstant('{localappdata}\JARVIS');
    ForceDirectories(DataDir);
    Content := 'WAKE_ENABLED=1' + #13#10 +
               'STT_MODEL=tiny' + #13#10 +
               'STT_LANG=auto' + #13#10 +
               'TTS_VOICE=ar-TN-HediNeural' + #13#10 +
               'TTS_RATE=+0%' + #13#10;
    if ZenKey <> '' then
    begin
      Content := Content + 'ZEN_API_KEY=' + ZenKey + #13#10;
    end;
    if Pin <> '' then
    begin
      Content := Content + 'JARVIS_PIN=' + Pin + #13#10;
    end;
    if LicKey <> '' then
    begin
      Content := Content + 'LICENSE_KEY=' + LicKey + #13#10;
    end;
    SaveStringToFile(DataDir + '\config.env', Content, False);
  end;
end;
