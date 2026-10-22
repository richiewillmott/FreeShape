; Inno Setup script for the FreeShape installer. build_exe.bat runs it automatically if Inno Setup is installed;
; or open it in Inno Setup and press Compile. Needs dist\FreeShape (from build_exe.bat) first.
#define AppName "FreeShape"
#define AppVersion "1.8.0"
#define AppRevision "r8"

[Setup]
AppId={{6F3B2C1D-9A4E-4C7B-8E21-5D0F3A9B7C42}
AppName={#AppName}
AppVersion={#AppVersion} ({#AppRevision})
AppVerName={#AppName} {#AppRevision}
AppPublisher=FreeShape
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\FreeShape.exe
UninstallDisplayName={#AppName}
SetupIconFile=..\freeshape\assets\freeshape.ico
WizardStyle=modern
; ~1 GB of files: moderate settings so the (32-bit) compiler doesn't run out of memory
Compression=lzma2/max
SolidCompression=yes
LZMAUseSeparateProcess=yes
LZMADictionarySize=32768
LZMANumBlockThreads=1
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=FreeShape-Setup-{#AppRevision}
; install for everyone, or just for you (no admin needed)
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ChangesAssociations=yes

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"
Name: "assoc"; Description: "Open .fshape, .fasm and .fdwg files with FreeShape"; GroupDescription: "Files:"
Name: "stepwith"; Description: "Add FreeShape to 'Open with' for STEP files"; GroupDescription: "Files:"

[Files]
Source: "..\dist\FreeShape\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\FreeShape.exe"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\FreeShape.exe"; Tasks: desktopicon

[Registry]
; .fshape  (Part Studio)
Root: HKA; Subkey: "Software\Classes\.fshape"; ValueType: string; ValueName: ""; ValueData: "FreeShape.PartStudio"; Flags: uninsdeletevalue; Tasks: assoc
Root: HKA; Subkey: "Software\Classes\.fcad"; ValueType: string; ValueName: ""; ValueData: "FreeShape.PartStudio"; Flags: uninsdeletevalue; Tasks: assoc
Root: HKA; Subkey: "Software\Classes\FreeShape.PartStudio"; ValueType: string; ValueName: ""; ValueData: "FreeShape Part Studio"; Flags: uninsdeletekey; Tasks: assoc
Root: HKA; Subkey: "Software\Classes\FreeShape.PartStudio\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\_internal\freeshape\assets\fshape_doc.ico"; Tasks: assoc
Root: HKA; Subkey: "Software\Classes\FreeShape.PartStudio\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\FreeShape.exe"" ""%1"""; Tasks: assoc
; .fasm  (Assembly)
Root: HKA; Subkey: "Software\Classes\.fasm"; ValueType: string; ValueName: ""; ValueData: "FreeShape.Assembly"; Flags: uninsdeletevalue; Tasks: assoc
Root: HKA; Subkey: "Software\Classes\FreeShape.Assembly"; ValueType: string; ValueName: ""; ValueData: "FreeShape Assembly"; Flags: uninsdeletekey; Tasks: assoc
Root: HKA; Subkey: "Software\Classes\FreeShape.Assembly\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\_internal\freeshape\assets\fasm_doc.ico"; Tasks: assoc
Root: HKA; Subkey: "Software\Classes\FreeShape.Assembly\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\FreeShape.exe"" ""%1"""; Tasks: assoc
; .fdwg  (Drawing)
Root: HKA; Subkey: "Software\Classes\.fdwg"; ValueType: string; ValueName: ""; ValueData: "FreeShape.Drawing"; Flags: uninsdeletevalue; Tasks: assoc
Root: HKA; Subkey: "Software\Classes\FreeShape.Drawing"; ValueType: string; ValueName: ""; ValueData: "FreeShape Drawing"; Flags: uninsdeletekey; Tasks: assoc
Root: HKA; Subkey: "Software\Classes\FreeShape.Drawing\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\_internal\freeshape\assets\fdwg_doc.ico"; Tasks: assoc
Root: HKA; Subkey: "Software\Classes\FreeShape.Drawing\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\FreeShape.exe"" ""%1"""; Tasks: assoc
; STEP files: offered in "Open with" (doesn't take over whatever opens them now)
Root: HKA; Subkey: "Software\Classes\FreeShape.Step"; ValueType: string; ValueName: ""; ValueData: "STEP model"; Flags: uninsdeletekey; Tasks: stepwith
Root: HKA; Subkey: "Software\Classes\FreeShape.Step\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\FreeShape.exe"" ""%1"""; Tasks: stepwith
Root: HKA; Subkey: "Software\Classes\.step\OpenWithProgids"; ValueType: string; ValueName: "FreeShape.Step"; ValueData: ""; Flags: uninsdeletevalue; Tasks: stepwith
Root: HKA; Subkey: "Software\Classes\.stp\OpenWithProgids"; ValueType: string; ValueName: "FreeShape.Step"; ValueData: ""; Flags: uninsdeletevalue; Tasks: stepwith

[Run]
Filename: "{app}\FreeShape.exe"; Description: "Start FreeShape now"; Flags: nowait postinstall skipifsilent
