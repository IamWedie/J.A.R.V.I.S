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
const
  WeakPinsList =
    '000000,111111,222222,333333,444444,555555,666666,777777,888888,999999,' +
    '123456,654321,112233,0910,1234,4321,9876,2580,1004,1212,1122,6969,' +
    '1590,0101,0011,1010,12345,54321,0000';

var
  KeyPage: TInputQueryWizardPage;
  LicPage: TInputQueryWizardPage;

{ Returns True when Pin matches a common weak default (exact comma-delimited token). }
function IsWeakPin(const Pin: String): Boolean;
var
  S: String;
begin
  S := ',' + WeakPinsList + ',';
  Result := Pos(',' + Pin + ',', S) > 0;
end;

function InternetOpen(lpszAgent: String; dwAccessType: Longint; lpszProxy: String; lpszProxyBypass: String; dwFlags: Longint): THandle;
  external 'InternetOpenA@wininet.dll stdcall';
function InternetOpenUrl(hInternet: THandle; lpszUrl: String; lpszHeaders: String; dwHeadersLength: Longint; dwFlags: Longint; dwContext: Longint): THandle;
  external 'InternetOpenUrlA@wininet.dll stdcall';
function InternetCloseHandle(hInternet: THandle): Boolean;
  external 'InternetCloseHandle@wininet.dll stdcall';
function HttpQueryInfo(hRequest: THandle; dwInfoLevel: Longint; lpBuffer: String; var lpdwBufferLength: Longint; lpdwIndex: Longint): Boolean;
  external 'HttpQueryInfoA@wininet.dll stdcall';

{ Returns an error message on failure, or empty string when the Zen key is valid. }
function ValidateZenKey(const Key: String): String;
var
  hSession, hReq: THandle;
  Headers: String;
  Status: String;
  BufLen: Longint;
begin
  Result := '';
  hSession := InternetOpen('JARVIS', 0, '', '', 0);
  if hSession = 0 then
  begin
    Result := 'Could not initialize network. Check your connection and try again.';
    Exit;
  end;
  Headers := 'Authorization: Bearer ' + Key + #13#10;
  hReq := InternetOpenUrl(hSession, 'https://opencode.ai/zen/v1/models', Headers, Length(Headers),
    $80000000 or $800000, 0);
  if hReq = 0 then
  begin
    InternetCloseHandle(hSession);
    Result := 'The Zen API key was rejected or could not be reached. Check the key and your internet connection.';
    Exit;
  end;
  Status := '';
  BufLen := 0;
  HttpQueryInfo(hReq, 19, Status, BufLen, 0);
  SetLength(Status, BufLen);
  HttpQueryInfo(hReq, 19, Status, BufLen, 0);
  InternetCloseHandle(hReq);
  InternetCloseHandle(hSession);
  Status := Trim(Status);
  if Status = '200' then
    Result := ''
  else if Status = '0' then
    Result := 'The Zen API key was rejected (no valid response). Check the key.'
  else
    Result := 'The Zen API rejected the key (HTTP ' + Status + '). Check the key and try again.';
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
  ZenKey, Err: String;
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
      Exit;
    end;
    ZenKey := Trim(KeyPage.Edits[0].Text);
    if ZenKey <> '' then
    begin
      Err := ValidateZenKey(ZenKey);
      if Err <> '' then
      begin
        MsgBox('We could not validate your Zen API key.' + #13#10 + #13#10 + Err + #13#10 + #13#10 +
          'You can leave the field blank and set it up inside the app later.',
          mbError, MB_OK);
        Result := False;
        Exit;
      end;
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
               'STT_MODEL=tiny.en' + #13#10 +
               'STT_LANG=auto' + #13#10 +
               'TTS_VOICE=en-US-AndrewNeural' + #13#10 +
               'TTS_RATE=+30%' + #13#10;
    if ZenKey <> '' then
    begin
      Content := Content + 'ZEN_API_KEY=' + ZenKey + #13#10 +
                           'ZEN_KEY_VALIDATED=1' + #13#10;
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
